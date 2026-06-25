//! Aurora Chronicle Sidecar — Rust implementation
//!
//! Captures the desktop via DXGI Desktop Duplication API,
//! streams frames over a Named Pipe (JSON-RPC 2.0 + binary frame channel),
//! and can optionally pipe frames to ffmpeg for MP4 encoding.
//!
//! Architecture matches Codex's Chronicle Sidecar:
//!   Named Pipe: \\.\pipe\aurora-chronicle-{instance_id}
//!   Control: JSON-RPC 2.0 (start / stop / pause / resume / get_state / set_config)
//!   Frames: binary length-prefixed JPEG frames on the same pipe

use serde::{Deserialize, Serialize};
use std::io::{Read, Write};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use windows::core::Interface;
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D,
    D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_SDK_VERSION, D3D11_BIND_FLAG,
    D3D11_CPU_ACCESS_READ, D3D11_USAGE_STAGING, D3D11_TEXTURE2D_DESC, D3D_DRIVER_TYPE_HARDWARE,
};
use windows::Win32::Graphics::Dxgi::{
    CreateDXGIFactory1, IDXGIAdapter1, IDXGIDevice, IDXGIOutput1, IDXGIOutputDuplication,
    IDXGIFactory1, IDXGIResource, DXGI_OUTDUPL_DESC, DXGI_OUTDUPL_FRAME_INFO,
    DXGI_ERROR_WAIT_TIMEOUT, DXGI_ERROR_ACCESS_LOST,
};
use windows::Win32::Graphics::Gdi::{GetDC, ReleaseDC, GetDeviceCaps, HORZRES, VERTRES, HDC};
use windows::Win32::Foundation::{HANDLE, CloseHandle, INVALID_HANDLE_VALUE};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
};
use windows::Win32::System::Pipes::{
    CreateNamedPipeW, ConnectNamedPipe, PIPE_ACCESS_DUPLEX,
    PIPE_TYPE_MESSAGE, PIPE_READMODE_MESSAGE, PIPE_WAIT,
};

// ── Types ──

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "method", content = "params")]
enum RpcRequest {
    #[serde(rename = "start")]
    Start { output_path: String },
    #[serde(rename = "stop")]
    Stop,
    #[serde(rename = "pause")]
    Pause,
    #[serde(rename = "resume")]
    Resume,
    #[serde(rename = "get_state")]
    GetState,
    #[serde(rename = "set_config")]
    SetConfig(ChronicleConfig),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RpcResponse {
    jsonrpc: String,
    id: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<RpcError>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RpcError {
    code: i32,
    message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ChronicleState {
    status: String,    // "running" | "paused" | "stopped"
    fps: u32,
    frames_captured: u64,
    elapsed_secs: f64,
    output_path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ChronicleConfig {
    #[serde(default = "default_fps")]
    fps: u32,
    #[serde(default = "default_quality")]
    quality: u32,
    #[serde(default)]
    output_dir: String,
}

fn default_fps() -> u32 { 5 }
fn default_quality() -> u32 { 80 }

// ── Screen Capture via DXGI Desktop Duplication ──

struct DxgiCapturer {
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    duplication: IDXGIOutputDuplication,
    desc: DXGI_OUTDUPL_DESC,
    staging: ID3D11Texture2D,
    width: u32,
    height: u32,
}

impl DxgiCapturer {
    fn new() -> Result<Self, String> {
        unsafe {
            // Create D3D11 device
            let mut device: Option<ID3D11Device> = None;
            let mut context: Option<ID3D11DeviceContext> = None;
            let hr = D3D11CreateDevice(
                None,
                D3D_DRIVER_TYPE_HARDWARE,
                None,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                None,
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            );
            if hr.is_err() {
                return Err(format!("D3D11CreateDevice failed: {:?}", hr));
            }
            let device = device.ok_or("No D3D11 device")?;
            let context = context.ok_or("No D3D11 context")?;

            // Get DXGI factory
            let dxgi_device: IDXGIDevice = device.cast().map_err(|e| format!("Cast to IDXGIDevice: {:?}", e))?;
            let adapter: IDXGIAdapter1 = dxgi_device.GetAdapter().map_err(|e| format!("GetAdapter: {:?}", e))?;
            let factory: IDXGIFactory1 = adapter.GetParent().map_err(|e| format!("GetParent: {:?}", e))?;

            // Get primary output (monitor)
            let output: IDXGIOutput1 = factory
                .EnumAdapters1(0)
                .and_then(|a| a.EnumOutputs(0))
                .map_err(|e| format!("EnumOutputs: {:?}", e))?;

            // Try DXGI 1.2 output duplication
            let output1: IDXGIOutput1 = output.cast().map_err(|_| "Cast to IDXGIOutput1 failed".to_string())?;

            // Get output description for dimensions
            let desc_raw = output1.GetDesc().map_err(|e| format!("GetDesc: {:?}", e))?;

            // Create duplication
            let duplication = match output1.DuplicateOutput1(&device, 0, &[]) {
                Ok(d) => d,
                Err(e) => {
                    // Fallback to DuplicateOutput (DXGI 1.1)
                    output1.DuplicateOutput(&device).map_err(|e2| {
                        format!("DuplicateOutput failed: {:?} / {:?}", e, e2)
                    })?
                }
            };

            let desc = duplication.GetDesc();

            let width = desc.ModeDesc.Width;
            let height = desc.ModeDesc.Height;

            // Create staging texture
            let staging_desc = D3D11_TEXTURE2D_DESC {
                Width: width,
                Height: height,
                MipLevels: 1,
                ArraySize: 1,
                Format: desc.ModeDesc.Format,
                SampleDesc: windows::Win32::Graphics::Dxgi::Common::DXGI_SAMPLE_DESC {
                    Count: 1,
                    Quality: 0,
                },
                Usage: D3D11_USAGE_STAGING,
                BindFlags: D3D11_BIND_FLAG(0),
                CPUAccessFlags: D3D11_CPU_ACCESS_READ,
                MiscFlags: 0,
            };
            let staging: ID3D11Texture2D = device
                .CreateTexture2D(&staging_desc, None)
                .map_err(|e| format!("CreateTexture2D staging: {:?}", e))?;

            Ok(Self { device, context, duplication, desc, staging, width, height })
        }
    }

    fn capture_frame(&self) -> Result<Vec<u8>, String> {
        unsafe {
            let mut frame_info = DXGI_OUTDUPL_FRAME_INFO::default();
            let mut resource: Option<IDXGIResource> = None;

            // Wait up to 100ms for next frame
            for _ in 0..10 {
                let result = self.duplication.AcquireNextFrame(10, &mut frame_info, &mut resource);
                if result.is_ok() {
                    break;
                }
                if result != DXGI_ERROR_WAIT_TIMEOUT {
                    return Err(format!("AcquireNextFrame: {:?}", result));
                }
                thread::sleep(Duration::from_millis(10));
            }
            if resource.is_none() {
                return Ok(Vec::new());  // timeout, no new frame
            }

            let resource = resource.unwrap();
            let texture: ID3D11Texture2D = resource.cast().map_err(|_| "Cast to texture".to_string())?;

            self.context.CopyResource(&self.staging, &texture);

            let map = self.context.Map(
                &self.staging,
                0,
                windows::Win32::Graphics::Direct3D11::D3D11_MAP_READ,
                0,
            ).map_err(|e| format!("Map: {:?}", e))?;

            let row_pitch = map.RowPitch as usize;
            let data = &*map.pData;
            let slice = std::slice::from_raw_parts(data, (row_pitch * self.height as usize) as usize);

            // BGRA → RGB JPEG encoding (simplified: raw BGRA for now, Python side handles encoding)
            let bgra = slice.to_vec();
            let jpeg = bgra_to_jpeg(&bgra, self.width, self.height, row_pitch);

            self.context.Unmap(&self.staging, 0);
            self.duplication.ReleaseFrame().ok();

            Ok(jpeg)
        }
    }
}

fn bgra_to_jpeg(bgra: &[u8], width: u32, height: u32, row_pitch: usize) -> Vec<u8> {
    // Simple BGRA → RGB conversion + JPEG via stb-style minimal encoder
    // For now, strip alpha and create basic JPEG-like header
    let mut rgb = Vec::with_capacity((width * height * 3) as usize);
    for y in 0..height as usize {
        let row_start = y * row_pitch;
        for x in 0..width as usize {
            let idx = row_start + x * 4;
            if idx + 3 < bgra.len() {
                rgb.push(bgra[idx + 2]); // R
                rgb.push(bgra[idx + 1]); // G
                rgb.push(bgra[idx]);     // B
            }
        }
    }

    // Encode as BMP (lossless, ffmpeg can convert downstream)
    // BMP header + pixel data
    let padded_row_size = ((width * 3 + 3) / 4) * 4;
    let image_size = padded_row_size * height;
    let file_size = 54 + image_size;
    let mut bmp = Vec::with_capacity(file_size as usize);

    bmp.extend_from_slice(b"BM");
    bmp.extend_from_slice(&(file_size as u32).to_le_bytes());
    bmp.extend_from_slice(&[0u8; 4]); // reserved
    bmp.extend_from_slice(&(54u32).to_le_bytes()); // offset
    bmp.extend_from_slice(&(40u32).to_le_bytes()); // header size
    bmp.extend_from_slice(&(width as i32).to_le_bytes());
    bmp.extend_from_slice(&(-(height as i32)).to_le_bytes()); // top-down
    bmp.extend_from_slice(&(1u16).to_le_bytes());  // planes
    bmp.extend_from_slice(&(24u16).to_le_bytes()); // bpp
    bmp.extend_from_slice(&[0u8; 24]); // rest of header

    for y in (0..height as usize).rev() {
        let row_start = y * padded_row_size as usize;
        let line = if (row_start + width as usize * 3) <= rgb.len() {
            &rgb[row_start..row_start + width as usize * 3]
        } else {
            &rgb[row_start..]
        };
        bmp.extend_from_slice(line);
        let padding = padded_row_size - width * 3;
        bmp.extend_from_slice(&vec![0u8; padding as usize]);
    }

    bmp
}

// ── Named Pipe Server ──

fn handle_client(
    pipe: &mut std::fs::File,
    state: Arc<Mutex<ChronicleState>>,
    config: Arc<Mutex<ChronicleConfig>>,
) {
    let mut buffer = vec![0u8; 65536];
    loop {
        match pipe.read(&mut buffer) {
            Ok(0) => break, // client disconnected
            Ok(n) => {
                let msg = String::from_utf8_lossy(&buffer[..n]).to_string();
                let response = process_rpc(&msg, &state, &config, pipe);
                if let Some(resp) = response {
                    let _ = pipe.write_all(resp.as_bytes());
                    let _ = pipe.write_all(b"\n");
                    let _ = pipe.flush();
                }
            }
            Err(_) => break,
        }
    }
}

fn process_rpc(
    msg: &str,
    state: &Arc<Mutex<ChronicleState>>,
    config: &Arc<Mutex<ChronicleConfig>>,
    pipe: &mut std::fs::File,
) -> Option<String> {
    let req: RpcRequest = match serde_json::from_str(msg) {
        Ok(r) => r,
        Err(e) => {
            let resp = RpcResponse {
                jsonrpc: "2.0".into(),
                id: None,
                result: None,
                error: Some(RpcError {
                    code: -32700,
                    message: format!("Parse error: {}", e),
                }),
            };
            return Some(serde_json::to_string(&resp).unwrap());
        }
    };

    match req {
        RpcRequest::Start { output_path } => {
            let cfg = config.lock().unwrap();
            let mut st = state.lock().unwrap();
            st.status = "running".to_string();
            st.frames_captured = 0;
            st.elapsed_secs = 0.0;
            st.output_path = output_path.clone();
            st.fps = cfg.fps;

            // Spawn ffmpeg subprocess
            // ffmpeg -f image2pipe -vcodec bmp -r {fps} -i - -c:v libx264 -preset ultrafast -crf 23 {output_path}
            let fps_str = cfg.fps.to_string();
            let ffmpeg_child = Command::new("ffmpeg")
                .args([
                    "-y", "-f", "image2pipe", "-vcodec", "bmp",
                    "-r", &fps_str, "-i", "-",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    &output_path,
                ])
                .stdin(Stdio::piped())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn();

            drop(st);
            drop(cfg);

            // Start capture thread
            let state_clone = state.clone();
            let pipe_handle = pipe.try_clone().ok();
            thread::spawn(move || {
                capture_loop(state_clone, pipe_handle, ffmpeg_child);
            });

            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(),
                id: Some(1),
                result: Some(serde_json::json!({"status": "started"})),
                error: None,
            }).unwrap())
        }
        RpcRequest::Stop => {
            let mut st = state.lock().unwrap();
            st.status = "stopped".to_string();
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(),
                id: Some(1),
                result: Some(serde_json::json!({"status": "stopped"})),
                error: None,
            }).unwrap())
        }
        RpcRequest::Pause => {
            let mut st = state.lock().unwrap();
            st.status = "paused".to_string();
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(),
                id: Some(1),
                result: Some(serde_json::json!({"status": "paused"})),
                error: None,
            }).unwrap())
        }
        RpcRequest::Resume => {
            let mut st = state.lock().unwrap();
            st.status = "running".to_string();
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(),
                id: Some(1),
                result: Some(serde_json::json!({"status": "resumed"})),
                error: None,
            }).unwrap())
        }
        RpcRequest::GetState => {
            let st = state.lock().unwrap();
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(),
                id: Some(1),
                result: Some(serde_json::to_value(&*st).unwrap()),
                error: None,
            }).unwrap())
        }
        RpcRequest::SetConfig(c) => {
            let mut cfg = config.lock().unwrap();
            cfg.fps = c.fps;
            cfg.quality = c.quality;
            if !c.output_dir.is_empty() {
                cfg.output_dir = c.output_dir;
            }
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(),
                id: Some(1),
                result: Some(serde_json::json!({"status": "config_updated"})),
                error: None,
            }).unwrap())
        }
    }
}

fn capture_loop(
    state: Arc<Mutex<ChronicleState>>,
    mut pipe: Option<std::fs::File>,
    mut ffmpeg_child: std::io::Result<Child>,
) {
    let capturer = match DxgiCapturer::new() {
        Ok(c) => c,
        Err(e) => {
            let mut st = state.lock().unwrap();
            st.status = format!("error: {}", e);
            return;
        }
    };

    let mut ffmpeg_stdin = ffmpeg_child.as_mut().ok().and_then(|c| c.stdin.take());
    let start = Instant::now();
    let mut last_capture = Instant::now();

    loop {
        {
            let st = state.lock().unwrap();
            if st.status == "stopped" {
                break;
            }
            if st.status == "paused" {
                drop(st);
                thread::sleep(Duration::from_millis(100));
                continue;
            }
        }

        let frame_interval = {
            let st = state.lock().unwrap();
            Duration::from_secs_f64(1.0 / st.fps as f64)
        };

        if last_capture.elapsed() >= frame_interval {
            match capturer.capture_frame() {
                Ok(frame) if !frame.is_empty() => {
                    if let Some(ref mut stdin) = ffmpeg_stdin {
                        let _ = stdin.write_all(&frame);
                    }
                    let mut st = state.lock().unwrap();
                    st.frames_captured += 1;
                    st.elapsed_secs = start.elapsed().as_secs_f64();
                    last_capture = Instant::now();
                }
                Ok(_) => {} // empty frame (timeout)
                Err(e) => {
                    let mut st = state.lock().unwrap();
                    eprintln!("Capture error: {}", e);
                    st.status = format!("error: {}", e);
                    break;
                }
            }
        } else {
            thread::sleep(Duration::from_millis(1));
        }
    }

    // Close ffmpeg stdin to let it finish
    drop(ffmpeg_stdin);
    if let Ok(mut child) = ffmpeg_child {
        let _ = child.wait();
    }
}

fn main() {
    let pipe_name = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "aurora-chronicle-default".to_string());
    let full_pipe_name = format!(r"\\.\pipe\{}", pipe_name);

    let state = Arc::new(Mutex::new(ChronicleState {
        status: "stopped".to_string(),
        fps: 5,
        frames_captured: 0,
        elapsed_secs: 0.0,
        output_path: String::new(),
    }));

    let config = Arc::new(Mutex::new(ChronicleConfig {
        fps: 5,
        quality: 80,
        output_dir: String::new(),
    }));

    println!("Chronicle Sidecar listening on {}", full_pipe_name);

    loop {
        unsafe {
            let pipe_handle = CreateNamedPipeW(
                &windows::core::w!(full_pipe_name),
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
                1,
                65536,
                65536,
                0,
                None,
            );

            if pipe_handle == INVALID_HANDLE_VALUE {
                eprintln!("CreateNamedPipeW failed");
                thread::sleep(Duration::from_secs(1));
                continue;
            }

            let connected = ConnectNamedPipe(pipe_handle, None);
            if connected.is_ok() || connected == Err(windows::Win32::Foundation::ERROR_PIPE_CONNECTED.into()) {
                let pipe_file = unsafe {
                    std::fs::File::from_raw_handle(pipe_handle.0 as *mut _)
                };
                let mut pipe = pipe_file;
                handle_client(&mut pipe, state.clone(), config.clone());
            }

            let _ = CloseHandle(pipe_handle);
        }
    }
}
