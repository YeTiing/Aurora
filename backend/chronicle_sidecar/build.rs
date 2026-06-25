// Chronicle Sidecar build script
// Auto-detects ffmpeg at build time.
fn main() {
    // Check if ffmpeg is available
    if let Ok(_) = std::process::Command::new("ffmpeg").arg("-version").output() {
        println!("cargo:rustc-cfg=feature=\"ffmpeg\"");
        println!("cargo:warning=ffmpeg found — MP4 encoding enabled");
    } else {
        println!("cargo:warning=ffmpeg not found — install ffmpeg for MP4 output");
    }
}
