fn main() {
    #[cfg(feature = "imx6ull-mlp")]
    {
        let source = "../experiments/imx6ull-policy/policy.c";
        println!("cargo:rerun-if-changed={source}");
        println!("cargo:rerun-if-changed=../experiments/imx6ull-policy/policy.h");
        let mut build = cc::Build::new();
        build.file(source).opt_level(3).flag("-ffp-contract=off");
        if std::env::var("CARGO_CFG_TARGET_ARCH").as_deref() == Ok("arm") {
            build.flag("-mfpu=neon-vfpv4");
        }
        build.compile("duck_mlp");
        println!("cargo:rustc-link-lib=m");
    }
}
