use serde::Deserialize;
use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io::{self, Write};
use std::path::Path;

#[derive(Debug, Deserialize)]
struct CardFrontmatter {
    name: Option<String>,
    description: Option<String>,
    status: Option<String>,
    metadata: Option<Metadata>,
}

#[derive(Debug, Deserialize)]
struct Metadata {
    #[serde(rename = "type")]
    r#type: Option<String>,
}

struct Card {
    name: String,
    description: String,
    card_type: Option<String>,
}

/// Extract YAML frontmatter between the first `---` and the second `---`.
fn extract_frontmatter(content: &str) -> Option<String> {
    let lines: Vec<&str> = content.lines().collect();
    if lines.is_empty() || lines[0].trim() != "---" {
        return None;
    }
    let end = lines[1..].iter().position(|l| l.trim() == "---")?;
    Some(lines[1..1 + end].join("\n"))
}

/// Scan a directory for `*.md` files, parse their frontmatter, and return
/// active cards sorted by filename.
fn scan_dir(dir: &Path) -> Vec<Card> {
    let mut cards = Vec::new();

    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return cards,
    };

    let mut paths: Vec<_> = entries
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map_or(false, |ext| ext == "md"))
        .collect();
    paths.sort();

    for path in paths {
        let content = match fs::read_to_string(&path) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("warn: {}: cannot read file: {}", path.display(), e);
                continue;
            }
        };

        let fm_str = match extract_frontmatter(&content) {
            Some(fm) => fm,
            None => {
                eprintln!("warn: {}: no YAML frontmatter, skipping", path.display());
                continue;
            }
        };

        let fm: CardFrontmatter = match serde_yaml::from_str(&fm_str) {
            Ok(f) => f,
            Err(e) => {
                eprintln!(
                    "warn: {}: failed to parse frontmatter: {}, skipping",
                    path.display(),
                    e
                );
                continue;
            }
        };

        let name = match fm.name {
            Some(n) => n,
            None => {
                eprintln!(
                    "warn: {}: missing required field 'name', skipping",
                    path.display()
                );
                continue;
            }
        };

        let description = match fm.description {
            Some(d) => d,
            None => {
                eprintln!(
                    "warn: {}: missing required field 'description', skipping",
                    path.display()
                );
                continue;
            }
        };

        // Only keep active cards (status is "active" or missing)
        match fm.status.as_deref() {
            None | Some("active") => {}
            _ => continue,
        }

        let card_type = fm.metadata.and_then(|m| m.r#type);

        cards.push(Card {
            name,
            description,
            card_type,
        });
    }

    cards
}

/// Format a section (System / Project) as bullet list, grouping by
/// metadata.type when present.
fn format_section(label: &str, cards: &[Card]) -> Option<String> {
    if cards.is_empty() {
        return None;
    }

    let mut lines = vec![format!("## {}", label)];

    let mut untyped: Vec<&Card> = Vec::new();
    let mut typed: BTreeMap<&str, Vec<&Card>> = BTreeMap::new();

    for card in cards {
        match &card.card_type {
            Some(t) => typed.entry(t.as_str()).or_default().push(card),
            None => untyped.push(card),
        }
    }

    for card in &untyped {
        lines.push(format!("- {} — {}", card.name, card.description));
    }

    for (t, group) in &typed {
        lines.push(format!("### {}", t));
        for card in group {
            lines.push(format!("- {} — {}", card.name, card.description));
        }
    }

    Some(lines.join("\n"))
}

fn main() {
    if std::env::args().any(|a| a == "--version") {
        println!("{}", env!("CARGO_PKG_VERSION"));
        return;
    }

    let args: Vec<String> = env::args().collect();
    let mut system_dir: Option<String> = None;
    let mut project_dir: Option<String> = None;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--system" => {
                if i + 1 < args.len() {
                    system_dir = Some(args[i + 1].clone());
                    i += 2;
                } else {
                    i += 1;
                }
            }
            "--project" => {
                if i + 1 < args.len() {
                    project_dir = Some(args[i + 1].clone());
                    i += 2;
                } else {
                    i += 1;
                }
            }
            _ => {
                i += 1;
            }
        }
    }

    let system_cards = system_dir
        .as_deref()
        .map(|d| scan_dir(Path::new(d)))
        .unwrap_or_default();
    let project_cards = project_dir
        .as_deref()
        .map(|d| scan_dir(Path::new(d)))
        .unwrap_or_default();

    if system_cards.is_empty() && project_cards.is_empty() {
        return;
    }

    let mut parts: Vec<String> = Vec::new();
    if let Some(section) = format_section("System", &system_cards) {
        parts.push(section);
    }
    if let Some(section) = format_section("Project", &project_cards) {
        parts.push(section);
    }

    let content = parts.join("\n\n");
    let total = system_cards.len() + project_cards.len();

    let sys_dir_display = system_dir.as_deref().unwrap_or("(not set)");
    let proj_dir_display = project_dir.as_deref().unwrap_or("(not set)");

    let injection = format!(
        "<memory-index>\n{}\n\nTotal: {} cards ({} system + {} project)\nSystem memory dir: {}\nProject memory dir: {}\n</memory-index>\n\nWhen you need the full content of a memory card, read the file directly from the memory/ directory. The index above only contains L1 descriptions — the full fact, evidence, and verification steps are in the card file itself.\nSystem memory dir: {}\nProject memory dir: {}",
        content,
        total,
        system_cards.len(),
        project_cards.len(),
        sys_dir_display,
        proj_dir_display,
        sys_dir_display,
        proj_dir_display,
    );

    #[derive(serde::Serialize)]
    struct HookSpecificOutput {
        #[serde(rename = "hookEventName")]
        hook_event_name: String,
        #[serde(rename = "additionalContext")]
        additional_context: String,
    }

    #[derive(serde::Serialize)]
    struct Output {
        #[serde(rename = "hookSpecificOutput")]
        hook_specific_output: HookSpecificOutput,
    }

    let output = Output {
        hook_specific_output: HookSpecificOutput {
            hook_event_name: "SessionStart".to_string(),
            additional_context: injection,
        },
    };

    let stdout = io::stdout();
    let mut handle = stdout.lock();
    serde_json::to_writer(&mut handle, &output).unwrap();
    writeln!(handle).unwrap();
}