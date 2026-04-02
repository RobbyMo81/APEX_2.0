/// VSH-001 integration test — shell execution engine
/// This test will not compile until ShellEngine is implemented in core/src/shell/mod.rs

#[tokio::test]
async fn shell_engine_runs_echo() {
    let engine = vashion_core::shell::ShellEngine::new();
    let result = engine.run("echo hello").await;
    assert!(result.is_ok(), "ShellEngine::run failed: {:?}", result.err());
    let output = result.unwrap();
    assert!(output.stdout.contains("hello"), "expected 'hello' in stdout, got: {}", output.stdout);
    assert_eq!(output.exit_code, 0);
}
