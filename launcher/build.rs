fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default() == "windows" {
        let mut res = winres::WindowsResource::new();
        res.set_icon("../assets/app-icon.ico");
        res.set("FileDescription", "EasyUniVPN Launcher");
        res.set("ProductName", "EasyUniVPN");
        res.set("LegalCopyright", "EasyUniVPN");
        res.compile().unwrap_or_else(|e| {
            // Don't fail the build - icon is cosmetic
            eprintln!("cargo:warning=winres icon embed failed: {e}");
        });
    }
}
