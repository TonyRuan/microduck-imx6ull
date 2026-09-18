fn main() {
    #[cfg(feature = "imx6ull-mlp")]
    {
        let source = "../experiments/imx6ull-policy/policy.c";
        println!("cargo:rerun-if-changed={source}");
        println!("cargo:rerun-if-changed=../experiments/imx6ull-policy/policy.h");
        let mut build = cc::Build::new();
        build.file(source).opt_level(3).flag("-ffp-contract=off");
        if std::env::var("CARGO_CFG_TARGET_ARCH").as_deref() == Ok("arm") {
            // cargo-zigbuild's generic ARM CPU explicitly disables NEON; -mfpu
            // alone does not override it. Zig uses underscores in CPU names,
            // while GCC/Clang use hyphens. Probe the spelling, never the ISA away.
            let cpu = if build
                .is_flag_supported("-mcpu=cortex_a7")
                .expect("cannot probe Cortex-A7 compiler flag")
            {
                "-mcpu=cortex_a7"
            } else {
                "-mcpu=cortex-a7"
            };
            build
                .flag(cpu)
                .flag("-mfpu=neon-vfpv4")
                .define("DUCK_REQUIRE_NEON", "1");
        }
        build.compile("duck_mlp");
        println!("cargo:rustc-link-lib=m");
    }
}
