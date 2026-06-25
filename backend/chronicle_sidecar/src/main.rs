//! Aurora Chronicle Sidecar — Rust DXGI screen capture + Named Pipe RPC
//!
//! Captures the desktop via DXGI Desktop Duplication API,
//! controlled over Named Pipe using JSON-RPC 2.0.
//! Pipes raw BGRA frames to ffmpeg for MP4 encoding.

use serde::{Deserialize, Serialize};
use std::io::{Read, Write};
use std::os::windows::io::FromRawHandle;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use windows::core::Interface;
use windows::Win32::Graphics::Direct3D::D3D_DRIVER_TYPE_HARDWARE;
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D,
    D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_SDK_VERSION,
    D3D11_CPU_ACCESS_READ, D3D11_USAGE_STAGING, D3D11_TEXTURE2D_DESC,
    D3D11_MAP_READ, D3D11_MAPPED_SUBRESOURCE, 
};
use windows::Win32::Graphics::Dxgi::{
    IDXGIDevice, IDXGIAdapter, IDXGIOutput, IDXGIOutput1, IDXGIOutputDuplication, DXGI_OUTDUPL_FRAME_INFO,
    DXGI_ERROR_WAIT_TIMEOUT, 
};
use windows::Win32::Foundation::{HANDLE, CloseHandle, INVALID_HANDLE_VALUE, HMODULE};
use windows::Win32::System::Pipes::{
    CreateNamedPipeW, ConnectNamedPipe,
    PIPE_TYPE_MESSAGE, PIPE_READMODE_MESSAGE, PIPE_WAIT,
};
const PIPE_ACCESS_DUPLEX: u32 = 0x00000003;


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
    status: String,
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

// ── Capture loop ──

fn capture_loop(state: Arc<Mutex<ChronicleState>>, ffmpeg_child: std::io::Result<Child>) {
    let mut ffmpeg_stdin = ffmpeg_child.ok().and_then(|c| c.stdin);
    let start = Instant::now();
    let mut last_capture = Instant::now();

    fn sync_capture() -> Result<Vec<u8>, String> {
        unsafe {
            // Create D3D11 device
            let mut device: Option<ID3D11Device> = None;
            let mut context: Option<ID3D11DeviceContext> = None;
            let adapter_ref: Option<&IDXGIAdapter> = None;
            let hr = D3D11CreateDevice(
                adapter_ref,
                D3D_DRIVER_TYPE_HARDWARE,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                None,
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            );
            if hr.is_err() { return Err(format!("D3D11CreateDevice: {:?}", hr)); }
            let device = device.ok_or("No D3D11 device")?;
            let context = context.ok_or("No D3D11 context")?;

            // Get DXGI output
            let dxgi_device: IDXGIDevice = device.cast().map_err(|_| "Cast IDXGIDevice")?;
            let adapter: IDXGIAdapter = dxgi_device.GetAdapter().map_err(|_| "GetAdapter")?;
            let output: IDXGIOutput = adapter.EnumOutputs(0).map_err(|_| "EnumOutputs")?;
            let output1: IDXGIOutput1 = output.cast().map_err(|_| "Cast IDXGIOutput1")?;

            let duplication = output1.DuplicateOutput(&device).map_err(|_| "DuplicateOutput failed")?;
            let desc = duplication.GetDesc();
            let width = desc.ModeDesc.Width;
            let height = desc.ModeDesc.Height;

            // Staging texture
            let staging_desc = D3D11_TEXTURE2D_DESC {
                Width: width,
                Height: height,
                MipLevels: 1,
                ArraySize: 1,
                Format: desc.ModeDesc.Format,
                SampleDesc: Default::default(),
                Usage: D3D11_USAGE_STAGING,
                BindFlags: 0u32,
                CPUAccessFlags: D3D11_CPU_ACCESS_READ.0 as u32,
                MiscFlags: 0,
            };
            let mut staging: Option<ID3D11Texture2D> = None;
            device.CreateTexture2D(&staging_desc, None, Some(&mut staging))
                .map_err(|_| "CreateTexture2D")?;
            let staging = staging.ok_or("No staging texture")?;

            // Acquire frame
            let mut frame_info = DXGI_OUTDUPL_FRAME_INFO::default();
            let mut resource: Option<windows::Win32::Graphics::Dxgi::IDXGIResource> = None;
            for _ in 0..10 {
                let r = duplication.AcquireNextFrame(10, &mut frame_info, &mut resource);
                if r.is_ok() { break; }
                if r == Err(DXGI_ERROR_WAIT_TIMEOUT.into()) { continue; }
                thread::sleep(Duration::from_millis(10));
            }

            if resource.is_none() { return Ok(Vec::new()); }
            let resource = resource.unwrap();
            let texture: ID3D11Texture2D = resource.cast().map_err(|_| "Cast texture")?;
            context.CopyResource(&staging, &texture);

            let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
            context.Map(&staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped))
                .map_err(|_| "Map failed")?;

            let row_pitch = mapped.RowPitch as usize;
            let data_ptr = mapped.pData as *mut u8;
            let slice = std::slice::from_raw_parts(data_ptr, row_pitch * height as usize);
            let bgra = slice.to_vec();
            let bmp = bgra_to_bmp(&bgra, width, height, row_pitch);

            context.Unmap(&staging, 0);
            duplication.ReleaseFrame().ok();

            Ok(bmp)
        }
    }

    loop {
        {
            let st = state.lock().unwrap();
            if st.status == "stopped" { break; }
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
            match sync_capture() {
                Ok(frame) if !frame.is_empty() => {
                    if let Some(ref mut stdin) = ffmpeg_stdin {
                        let _ = stdin.write_all(&frame);
                    }
                    let mut st = state.lock().unwrap();
                    st.frames_captured += 1;
                    st.elapsed_secs = start.elapsed().as_secs_f64();
                }
                Ok(_) => {}
                Err(e) => {
                    eprintln!("Capture error: {}", e);
                }
            }
            last_capture = Instant::now();
        } else {
            thread::sleep(Duration::from_millis(1));
        }
    }
    drop(ffmpeg_stdin);
}

fn bgra_to_bmp(bgra: &[u8], width: u32, height: u32, row_pitch: usize) -> Vec<u8> {
    let padded_row_size = ((width * 3 + 3) / 4) * 4;
    let image_size = padded_row_size * height;
    let file_size = 54 + image_size;
    let mut bmp = Vec::with_capacity(file_size as usize);
    bmp.extend_from_slice(b"BM");
    bmp.extend_from_slice(&file_size.to_le_bytes());
    bmp.extend_from_slice(&[0u8; 4]);
    bmp.extend_from_slice(&54u32.to_le_bytes());
    bmp.extend_from_slice(&40u32.to_le_bytes());
    bmp.extend_from_slice(&width.to_le_bytes());
    bmp.extend_from_slice(&height.to_le_bytes());
    bmp.extend_from_slice(&1u16.to_le_bytes());
    bmp.extend_from_slice(&24u16.to_le_bytes());
    bmp.extend_from_slice(&[0u8; 24]);

    for y in (0..height as usize).rev() {
        let src_start = y * row_pitch;
        for x in 0..width as usize {
            let si = src_start + x * 4;
            if si + 3 < bgra.len() {
                bmp.push(bgra[si + 2]); // R
                bmp.push(bgra[si + 1]); // G
                bmp.push(bgra[si]);     // B
            } else {
                bmp.extend_from_slice(&[0, 0, 0]);
            }
        }
        let row_end = bmp.len();
        let padding = (padded_row_size as usize).saturating_sub(row_end % padded_row_size as usize);
        bmp.extend_from_slice(&vec![0u8; padding]);
    }
    bmp
}

// ── Named Pipe Server ──

fn handle_client(
    pipe: &mut std::fs::File,
    state: Arc<Mutex<ChronicleState>>,
    config: Arc<Mutex<ChronicleConfig>>,
    ffmpeg_child: &mut Option<std::io::Result<Child>>,
) {
    let mut buf = vec![0u8; 65536];
    loop {
        match pipe.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                let msg = String::from_utf8_lossy(&buf[..n]).to_string();
                if let Some(resp) = process_rpc(&msg, &state, &config, ffmpeg_child) {
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
    ffmpeg_child: &mut Option<std::io::Result<Child>>,
) -> Option<String> {
    let req: RpcRequest = match serde_json::from_str(msg) {
        Ok(r) => r,
        Err(e) => {
            let resp = RpcResponse {
                jsonrpc: "2.0".into(), id: None, result: None,
                error: Some(RpcError { code: -32700, message: format!("Parse error: {}", e) }),
            };
            return Some(serde_json::to_string(&resp).unwrap());
        }
    };
    match req {
        RpcRequest::Start { output_path } => {
            let cfg = config.lock().unwrap();
            let mut st = state.lock().unwrap();
            st.status = "running".to_string();
            st.fps = cfg.fps;
            st.frames_captured = 0;
            st.elapsed_secs = 0.0;
            st.output_path = output_path.clone();
            drop(cfg);
            drop(st);

            let fps_str = state.lock().unwrap().fps.to_string();
            let child = Command::new("ffmpeg")
                .args(["-y", "-f", "image2pipe", "-vcodec", "bmp", "-r", &fps_str, "-i", "-",
                       "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", &output_path])
                .stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
            *ffmpeg_child = Some(child);

            let st2 = state.clone();
            if let Some(Ok(c)) = ffmpeg_child.take() {
                thread::spawn(move || capture_loop(st2, Ok(c)));
            }

            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(), id: Some(1),
                result: Some(serde_json::json!({"status": "started"})), error: None,
            }).unwrap())
        }
        RpcRequest::Stop => {
            let mut st = state.lock().unwrap();
            st.status = "stopped".to_string();
            *ffmpeg_child = None;
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(), id: Some(1),
                result: Some(serde_json::json!({"status": "stopped"})), error: None,
            }).unwrap())
        }
        RpcRequest::Pause => {
            state.lock().unwrap().status = "paused".to_string();
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(), id: Some(1),
                result: Some(serde_json::json!({"status": "paused"})), error: None,
            }).unwrap())
        }
        RpcRequest::Resume => {
            state.lock().unwrap().status = "running".to_string();
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(), id: Some(1),
                result: Some(serde_json::json!({"status": "resumed"})), error: None,
            }).unwrap())
        }
        RpcRequest::GetState => {
            let st = state.lock().unwrap();
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(), id: Some(1),
                result: Some(serde_json::to_value(&*st).unwrap()), error: None,
            }).unwrap())
        }
        RpcRequest::SetConfig(c) => {
            let mut cfg = config.lock().unwrap();
            cfg.fps = c.fps;
            cfg.quality = c.quality;
            if !c.output_dir.is_empty() { cfg.output_dir = c.output_dir; }
            Some(serde_json::to_string(&RpcResponse {
                jsonrpc: "2.0".into(), id: Some(1),
                result: Some(serde_json::json!({"status": "config_updated"})), error: None,
            }).unwrap())
        }
    }
}

fn main() {
    let pipe_id = std::env::args().nth(1).unwrap_or_else(|| "aurora-chronicle-default".into());
    let full_name = format!(r"\\.\pipe\{}", pipe_id);

    let state = Arc::new(Mutex::new(ChronicleState {
        status: "stopped".into(), fps: 5,
        frames_captured: 0, elapsed_secs: 0.0, output_path: String::new(),
    }));
    let config = Arc::new(Mutex::new(ChronicleConfig {
        fps: 5, quality: 80, output_dir: String::new(),
    }));

    // Encode pipe name to wide string
    let wide: Vec<u16> = full_name.encode_utf16().chain(std::iter::once(0)).collect();

    println!("# Chronicle Sidecar");
    println!("# Pipe: {}", full_name);

    loop {
        unsafe {
            let pipe_handle = CreateNamedPipeW(
                windows::core::PCWSTR::from_raw(wide.as_ptr()),
                windows::Win32::Storage::FileSystem::FILE_FLAGS_AND_ATTRIBUTES(PIPE_ACCESS_DUPLEX),
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
                1, 65536, 65536, 0, None,
            );
            if pipe_handle == INVALID_HANDLE_VALUE {
                eprintln!("CreateNamedPipeW failed");
                thread::sleep(Duration::from_secs(1));
                continue;
            }

            let _ = ConnectNamedPipe(pipe_handle, None);
            let mut pipe_file = std::fs::File::from_raw_handle(pipe_handle.0 as _);
            let mut ffmpeg_child: Option<std::io::Result<Child>> = None;
            handle_client(&mut pipe_file, state.clone(), config.clone(), &mut ffmpeg_child);
            let _ = CloseHandle(HANDLE(pipe_handle.0));
        }
    }
}
