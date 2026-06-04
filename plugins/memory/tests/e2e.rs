use serde_json::Value;
use std::fs;
use std::path::Path;
use std::process::Command;

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_claude-memory-scan")
}

fn write_card(dir: &Path, filename: &str, frontmatter: &str, body: &str) {
    let content = format!("---\n{}\n---\n\n{}", frontmatter, body);
    fs::write(dir.join(filename), content).unwrap();
}

fn run_scan(system_dir: Option<&Path>, project_dir: Option<&Path>) -> std::process::Output {
    let mut cmd = Command::new(binary());
    if let Some(d) = system_dir {
        cmd.arg("--system").arg(d);
    }
    if let Some(d) = project_dir {
        cmd.arg("--project").arg(d);
    }
    cmd.output().expect("failed to execute binary")
}

fn stdout_str(output: &std::process::Output) -> &str {
    std::str::from_utf8(&output.stdout).unwrap()
}

fn stderr_str(output: &std::process::Output) -> &str {
    std::str::from_utf8(&output.stderr).unwrap()
}

fn parse_json(output: &std::process::Output) -> Value {
    serde_json::from_str(stdout_str(output)).unwrap()
}

// ---------------------------------------------------------------------------
// 1. empty_dirs
// ---------------------------------------------------------------------------
#[test]
fn empty_dirs() {
    let sys = tempfile::tempdir().unwrap();
    let proj = tempfile::tempdir().unwrap();
    let out = run_scan(Some(sys.path()), Some(proj.path()));
    assert!(out.status.success());
    assert!(stdout_str(&out).is_empty());
}

// ---------------------------------------------------------------------------
// 2. system_only
// ---------------------------------------------------------------------------
#[test]
fn system_only() {
    let sys = tempfile::tempdir().unwrap();
    write_card(sys.path(), "a.md", "name: sys-card\ndescription: A system card", "body");

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("## System"));
    assert!(ctx.contains("- sys-card — A system card"));
    assert!(!ctx.contains("## Project"));
}

// ---------------------------------------------------------------------------
// 3. project_only
// ---------------------------------------------------------------------------
#[test]
fn project_only() {
    let proj = tempfile::tempdir().unwrap();
    write_card(proj.path(), "p.md", "name: proj-card\ndescription: A project card", "body");

    let out = run_scan(None, Some(proj.path()));
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("## Project"));
    assert!(ctx.contains("- proj-card — A project card"));
    assert!(!ctx.contains("## System"));
}

// ---------------------------------------------------------------------------
// 4. both_dirs
// ---------------------------------------------------------------------------
#[test]
fn both_dirs() {
    let sys = tempfile::tempdir().unwrap();
    let proj = tempfile::tempdir().unwrap();
    write_card(sys.path(), "s.md", "name: sys-card\ndescription: System card", "body");
    write_card(proj.path(), "p.md", "name: proj-card\ndescription: Project card", "body");

    let out = run_scan(Some(sys.path()), Some(proj.path()));
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("## System"));
    assert!(ctx.contains("## Project"));
    assert!(ctx.contains("- sys-card — System card"));
    assert!(ctx.contains("- proj-card — Project card"));
}

// ---------------------------------------------------------------------------
// 5. filter_deprecated
// ---------------------------------------------------------------------------
#[test]
fn filter_deprecated() {
    let sys = tempfile::tempdir().unwrap();
    write_card(sys.path(), "active.md", "name: active-card\ndescription: still active\nstatus: active", "body");
    write_card(sys.path(), "deprecated.md", "name: old-card\ndescription: should not appear\nstatus: deprecated", "body");

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("- active-card"));
    assert!(!ctx.contains("old-card"));
}

// ---------------------------------------------------------------------------
// 6. filter_stale
// ---------------------------------------------------------------------------
#[test]
fn filter_stale() {
    let sys = tempfile::tempdir().unwrap();
    write_card(sys.path(), "a.md", "name: active-card\ndescription: still active\nstatus: active", "body");
    write_card(sys.path(), "s.md", "name: stale-card\ndescription: should not appear\nstatus: stale", "body");

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("- active-card"));
    assert!(!ctx.contains("stale-card"));
}

// ---------------------------------------------------------------------------
// 7. no_status_defaults_active
// ---------------------------------------------------------------------------
#[test]
fn no_status_defaults_active() {
    let sys = tempfile::tempdir().unwrap();
    write_card(sys.path(), "nostatus.md", "name: implicit-active\ndescription: no status field", "body");

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("implicit-active"));
}

// ---------------------------------------------------------------------------
// 8. malformed_frontmatter
// ---------------------------------------------------------------------------
#[test]
fn malformed_frontmatter() {
    let sys = tempfile::tempdir().unwrap();
    write_card(sys.path(), "bad.md", ":: not valid yaml ::", "body");
    write_card(sys.path(), "good.md", "name: good\ndescription: still works", "body");

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let stderr = stderr_str(&out);
    assert!(stderr.contains("bad.md"), "stderr should warn about bad.md");
    assert!(stderr.contains("failed to parse frontmatter"), "stderr should mention parse failure");

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("- good — still works"), "good card should still appear");
}

// ---------------------------------------------------------------------------
// 9. description_in_body
// ---------------------------------------------------------------------------
#[test]
fn description_in_body() {
    let sys = tempfile::tempdir().unwrap();
    let frontmatter = "name: safe-card\ndescription: real description";
    let body = "description: this is in the body, not frontmatter";
    write_card(sys.path(), "card.md", frontmatter, body);

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("real description"));
    assert!(!ctx.contains("this is in the body"));
}

// ---------------------------------------------------------------------------
// 10. metadata_type_grouping
// ---------------------------------------------------------------------------
#[test]
fn metadata_type_grouping() {
    let sys = tempfile::tempdir().unwrap();
    write_card(
        sys.path(),
        "user-card.md",
        "name: user-pref\ndescription: User preference\nmetadata:\n  type: user",
        "body",
    );
    write_card(
        sys.path(),
        "ref-card.md",
        "name: ref-doc\ndescription: Reference doc\nmetadata:\n  type: reference",
        "body",
    );
    write_card(
        sys.path(),
        "plain.md",
        "name: plain-card\ndescription: No type card",
        "body",
    );

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    // Untyped card appears before typed groups
    assert!(ctx.contains("- plain-card — No type card"));
    // Typed cards are grouped under ### headers
    assert!(ctx.contains("### reference"));
    assert!(ctx.contains("- ref-doc — Reference doc"));
    assert!(ctx.contains("### user"));
    assert!(ctx.contains("- user-pref — User preference"));
}

// ---------------------------------------------------------------------------
// 11. name_from_yaml
// ---------------------------------------------------------------------------
#[test]
fn name_from_yaml() {
    let sys = tempfile::tempdir().unwrap();
    // filename is "file.md" but YAML name is "yaml-name"
    write_card(sys.path(), "file.md", "name: yaml-name\ndescription: Uses YAML name not filename", "body");

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("- yaml-name"));
    assert!(!ctx.contains("- file")); // filename NOT used as name
}

// ---------------------------------------------------------------------------
// 12. stats_footer
// ---------------------------------------------------------------------------
#[test]
fn stats_footer() {
    let sys = tempfile::tempdir().unwrap();
    let proj = tempfile::tempdir().unwrap();
    write_card(sys.path(), "s1.md", "name: s1\ndescription: sys card 1", "body");
    write_card(sys.path(), "s2.md", "name: s2\ndescription: sys card 2", "body");
    write_card(proj.path(), "p1.md", "name: p1\ndescription: proj card 1", "body");

    let out = run_scan(Some(sys.path()), Some(proj.path()));
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("Total: 3 cards (2 system + 1 project)"));
}

// ---------------------------------------------------------------------------
// 13. json_structure
// ---------------------------------------------------------------------------
#[test]
fn json_structure() {
    let sys = tempfile::tempdir().unwrap();
    write_card(sys.path(), "card.md", "name: test-card\ndescription: test", "body");

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let val: Value = parse_json(&out);
    assert_eq!(
        val["hookSpecificOutput"]["hookEventName"].as_str(),
        Some("SessionStart")
    );
    assert!(val["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .contains("test-card"));
}

// ---------------------------------------------------------------------------
// 14. utf8_chinese
// ---------------------------------------------------------------------------
#[test]
fn utf8_chinese() {
    let sys = tempfile::tempdir().unwrap();
    write_card(sys.path(), "zh.md", "name: 中文卡片\ndescription: 这是一张中文描述的卡片", "正文内容");

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("中文卡片"));
    assert!(ctx.contains("这是一张中文描述的卡片"));
    // Ensure Chinese is not escaped as \uXXXX
    assert!(!ctx.contains("\\u"), "Chinese should not be unicode-escaped");
}

// ---------------------------------------------------------------------------
// 15. dir_not_exist
// ---------------------------------------------------------------------------
#[test]
fn dir_not_exist() {
    let proj = tempfile::tempdir().unwrap();
    write_card(proj.path(), "p.md", "name: proj-card\ndescription: project card", "body");

    let out = run_scan(Some(Path::new("/nonexistent/dir/for/test")), Some(proj.path()));
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(ctx.contains("## Project"));
    assert!(ctx.contains("proj-card"));
    assert!(!ctx.contains("## System"));
    // No error on stderr for missing dir
    assert!(stderr_str(&out).is_empty(), "non-existent dir should be silent");
}

// ---------------------------------------------------------------------------
// 16. non_md_files_ignored
// ---------------------------------------------------------------------------
#[test]
fn non_md_files_ignored() {
    let sys = tempfile::tempdir().unwrap();
    write_card(sys.path(), "card.md", "name: md-card\ndescription: markdown card", "body");
    fs::write(sys.path().join("notes.txt"), "some text").unwrap();
    fs::write(
        sys.path().join("config.yaml"),
        "name: yaml-card\ndescription: not a card",
    )
    .unwrap();

    let out = run_scan(Some(sys.path()), None);
    assert!(out.status.success());

    let ctx = parse_json(&out)["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap()
        .to_string();
    // Only .md files are scanned
    assert!(ctx.contains("md-card"));
    assert!(!ctx.contains("yaml-card"));
    assert!(!ctx.contains("notes.txt"));
}

// ---------------------------------------------------------------------------
// 17. version_flag
// ---------------------------------------------------------------------------
#[test]
fn version_flag_prints_cargo_version() {
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_claude-memory-scan"))
        .arg("--version")
        .output()
        .expect("run binary");
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert_eq!(stdout.trim(), env!("CARGO_PKG_VERSION"));
}