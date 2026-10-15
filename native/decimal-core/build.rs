//! Build only the checksum-pinned bundled C library, without system discovery.

use std::env;
use std::fs;
use std::path::Path;
use std::process::Command;

use sha2::{Digest, Sha256};

const ARCHIVE: &str = "vendor/mpdecimal-4.0.1.tar.gz";
const SHA256: &str = "96d33abb4bb0070c7be0fed4246cd38416188325f820468214471938545b1ac8";

fn run(command: &mut Command) {
    let result = command
        .status()
        .expect("failed to start bundled mpdecimal build tool");
    assert!(
        result.success(),
        "bundled mpdecimal build command failed: {command:?}"
    );
}

fn main() {
    println!("cargo:rerun-if-changed={ARCHIVE}");
    println!("cargo:rerun-if-changed=decimal_shim.c");
    println!("cargo:rerun-if-changed=decimal_shim.h");
    println!("cargo:rerun-if-env-changed=CC");
    println!("cargo:rerun-if-env-changed=AR");
    println!("cargo:rerun-if-env-changed=CFLAGS");
    assert_eq!(
        env::var("CARGO_CFG_TARGET_POINTER_WIDTH").unwrap(),
        "64",
        "Decimal arithmetic require a 64-bit target"
    );
    let target = env::var("CARGO_CFG_TARGET_OS").unwrap();
    assert!(
        target == "linux" || target == "macos",
        "unsupported Decimal native target"
    );
    assert_eq!(
        env::var("HOST").unwrap(),
        env::var("TARGET").unwrap(),
        "build Decimal extensions on their native architecture"
    );

    let bytes = fs::read(ARCHIVE).expect("bundled mpdecimal source archive is required");
    assert_eq!(
        format!("{:x}", Sha256::digest(&bytes)),
        SHA256,
        "bundled mpdecimal source checksum mismatch"
    );
    let out = env::var_os("OUT_DIR").unwrap();
    let out = Path::new(&out);
    let source = out.join("mpdecimal-4.0.1");
    // Always start clean on a build-script rerun; source never leaves OUT_DIR.
    if source.exists() {
        fs::remove_dir_all(&source).expect("remove prior bundled mpdecimal build");
    }
    run(Command::new("tar").args(["-xzf", ARCHIVE, "-C"]).arg(out));

    let compiler = cc::Build::new().get_compiler();
    let mut cflags = compiler
        .args()
        .iter()
        .map(|arg| arg.to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join(" ");
    cflags.push_str(" -O2 -fPIC -fvisibility=hidden");
    let mut configure = Command::new("./configure");
    configure
        .current_dir(&source)
        .args(["--disable-shared", "--enable-static", "--disable-cxx"])
        .env("CC", compiler.path())
        .env("CFLAGS", &cflags);
    run(&mut configure);
    run(Command::new("make")
        .current_dir(&source)
        // Upstream appends make's CFLAGS after configured flags. Keep our PIC
        // and visibility suffix last even when the consumer sets CFLAGS.
        .env("CFLAGS", &cflags)
        .args(["-C", "libmpdec", "libmpdec.a"]));

    let library = source.join("libmpdec");
    assert!(
        library.join("libmpdec.a").is_file(),
        "bundled static library missing"
    );
    cc::Build::new()
        .file("decimal_shim.c")
        .include(&library)
        .pic(true)
        .flag("-fvisibility=hidden")
        .compile("dotmac_decimal_core_shim");
    // Shim first, then its static dependencies. No pkg-config/system fallback,
    // shared libmpdec, C++ library, install target, or network fetch is used.
    println!("cargo:rustc-link-search=native={}", library.display());
    println!("cargo:rustc-link-lib=static=mpdec");
    if target == "linux" {
        println!("cargo:rustc-link-lib=m");
    }
}
