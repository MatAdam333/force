//! Remote-host presence: <state>/remotes/<host>/status.json and snapshot.json, plus the local snapshot
//! (<state>/snapshot.json), for the header's other-host markers. Never panics on I/O errors.

use std::path::Path;

use crate::snapshot::{self, State};

/// One remote's connection status read from `<state>/remotes/<host>/status.json`.
#[derive(Clone, PartialEq, Debug)]
pub struct HostStatus {
    pub state: String,
    pub detail: String,
}

/// Read `<state>/remotes/<host>/status.json`. None when missing, unreadable, not JSON, or "state" not a
/// string. "detail" missing/non-string -> "".
pub fn read_status(state_dir: &Path, host: &str) -> Option<HostStatus> {
    let raw = std::fs::read_to_string(state_dir.join("remotes").join(host).join("status.json")).ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let obj = value.as_object()?;
    let state = obj.get("state")?.as_str()?.to_string();
    let detail = obj.get("detail").and_then(|v| v.as_str()).unwrap_or("").to_string();
    Some(HostStatus { state, detail })
}

/// One host (other than the one shown) that has something for you.
#[derive(Clone, PartialEq, Debug)]
pub struct OtherHost {
    /// "local" for this machine, else the remote's name (used for the switch action).
    pub name: String,
    /// what is drawn: local_label for "local", else the remote's name.
    pub label: String,
    /// live (non-archived) sessions in State::NeedsYou.
    pub needs: usize,
    /// live (non-archived) sessions in State::YourTurn.
    pub turn: usize,
}

/// The hosts OTHER than the one shown that have something for you (needs + turn > 0).
/// - shown != "": this machine is a candidate: <state>/snapshot.json, name "local", label local_label.
/// - every subdirectory of <state>/remotes (directory entries only), except the one named `shown`, whose
///   read_status(...).state == "connected": <state>/remotes/<name>/snapshot.json.
/// - skip a candidate whose snapshot::load is None or snapshot::is_stale(snap, now_ms).
/// - order: "local" first, then remotes sorted by name.
/// - missing <state>/remotes dir: just no remotes. Never panics on I/O errors.
pub fn other_hosts(state_dir: &Path, shown: &str, local_label: &str, now_ms: i64) -> Vec<OtherHost> {
    let mut out = Vec::new();

    if !shown.is_empty() {
        if let Some(h) = host_from_snapshot(&state_dir.join("snapshot.json"), "local", local_label, now_ms) {
            if h.needs + h.turn > 0 {
                out.push(h);
            }
        }
    }

    let remotes_dir = state_dir.join("remotes");
    let mut names: Vec<String> = Vec::new();
    if let Ok(rd) = std::fs::read_dir(&remotes_dir) {
        for entry in rd.flatten() {
            if entry.file_type().map_or(false, |t| t.is_dir()) {
                if let Some(n) = entry.file_name().to_str() {
                    if n != shown {
                        names.push(n.to_string());
                    }
                }
            }
        }
    }
    names.sort();
    for n in names {
        let connected = match read_status(state_dir, &n) {
            Some(s) => s.state == "connected",
            None => false,
        };
        if !connected {
            continue;
        }
        if let Some(h) = host_from_snapshot(&state_dir.join("remotes").join(&n).join("snapshot.json"), &n, &n, now_ms) {
            if h.needs + h.turn > 0 {
                out.push(h);
            }
        }
    }

    out
}

/// Parse a snapshot at `path` into an OtherHost; None when unparseable, stale, or with no live needs/turn.
fn host_from_snapshot(path: &Path, name: &str, label: &str, now_ms: i64) -> Option<OtherHost> {
    let snap = snapshot::load(path)?;
    if snapshot::is_stale(&snap, now_ms) {
        return None;
    }
    let mut needs = 0usize;
    let mut turn = 0usize;
    for s in &snap.sessions {
        if s.archived {
            continue;
        }
        match s.state {
            State::NeedsYou => needs += 1,
            State::YourTurn => turn += 1,
            _ => {}
        }
    }
    if needs + turn == 0 {
        return None;
    }
    Some(OtherHost {
        name: name.to_string(),
        label: label.to_string(),
        needs,
        turn,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;

    struct TestDir(std::path::PathBuf);
    impl TestDir {
        fn new(tag: &str) -> Self {
            let p = std::env::temp_dir().join(format!("cockpit-hosts-{}-{:?}", tag, std::process::id()));
            let _ = fs::remove_dir_all(&p);
            fs::create_dir_all(&p).unwrap();
            TestDir(p)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    const NOW: i64 = 1_790_000_000_000;

    fn session(name: &str, state: &str) -> serde_json::Value {
        json!({"name": name, "state": state})
    }

    fn write_snapshot(dir: &Path, rel: &str, now_ms: i64, sessions: serde_json::Value) {
        let path = dir.join(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, serde_json::to_string(&json!({"generated_ms": now_ms, "sessions": sessions})).unwrap()).unwrap();
    }

    fn write_status(dir: &Path, host: &str, state: &str, detail: &str) {
        let path = dir.join("remotes").join(host).join("status.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, format!(r#"{{"state":"{}","detail":"{}","at":1.0,"version":null}}"#, state, detail)).unwrap();
    }

    #[test]
    fn status_missing_invalid_valid() {
        let d = TestDir::new("status");
        // missing
        assert_eq!(read_status(d.path(), "a"), None);
        // invalid (not JSON)
        write_status(d.path(), "a", "connected", "");
        let path = d.path().join("remotes/a/status.json");
        fs::write(&path, "not json").unwrap();
        assert_eq!(read_status(d.path(), "a"), None);
        // valid
        fs::write(&path, r#"{"state":"offline","detail":"timeout"}"#).unwrap();
        assert_eq!(read_status(d.path(), "a"), Some(HostStatus { state: "offline".into(), detail: "timeout".into() }));
        // state not a string -> None
        fs::write(&path, r#"{"state":123}"#).unwrap();
        assert_eq!(read_status(d.path(), "a"), None);
        // detail missing -> ""
        fs::write(&path, r#"{"state":"connected"}"#).unwrap();
        assert_eq!(read_status(d.path(), "a"), Some(HostStatus { state: "connected".into(), detail: "".into() }));
    }

    #[test]
    fn other_hosts_shown_empty_connected_only() {
        let d = TestDir::new("shown-empty");
        // two remotes: a connected with counts, b offline with needs -> only a
        write_snapshot(d.path(), "remotes/a/snapshot.json", NOW, json!([
            session("s1", "NEEDS_YOU"),
            session("s2", "YOUR_TURN"),
            session("s3", "YOUR_TURN"),
        ]));
        write_status(d.path(), "a", "connected", "");
        write_snapshot(d.path(), "remotes/b/snapshot.json", NOW, json!([session("s1", "NEEDS_YOU")]));
        write_status(d.path(), "b", "offline", "");
        let out = other_hosts(d.path(), "", "", NOW);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].name, "a");
        assert_eq!(out[0].label, "a");
        assert_eq!(out[0].needs, 1);
        assert_eq!(out[0].turn, 2);
    }

    #[test]
    fn other_hosts_shown_b_includes_local_first_and_excludes_b() {
        let d = TestDir::new("shown-b");
        write_snapshot(d.path(), "snapshot.json", NOW, json!([session("me", "NEEDS_YOU")]));
        write_snapshot(d.path(), "remotes/a/snapshot.json", NOW, json!([session("s1", "YOUR_TURN")]));
        write_status(d.path(), "a", "connected", "");
        write_snapshot(d.path(), "remotes/b/snapshot.json", NOW, json!([session("s1", "NEEDS_YOU")]));
        write_status(d.path(), "b", "connected", "");
        let out = other_hosts(d.path(), "b", "THIS-MACHINE", NOW);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].name, "local");
        assert_eq!(out[0].label, "THIS-MACHINE");
        assert_eq!(out[0].needs, 1);
        assert_eq!(out[1].name, "a");
    }

    #[test]
    fn archived_not_counted_zero_skipped() {
        let d = TestDir::new("archived");
        // archived needs/turn not counted -> zero -> skipped
        write_snapshot(d.path(), "remotes/a/snapshot.json", NOW, json!([
            {"name":"s1","state":"NEEDS_YOU","archived":true},
            {"name":"s2","state":"YOUR_TURN","archived":true},
            {"name":"s3","state":"IDLE"},
        ]));
        write_status(d.path(), "a", "connected", "");
        assert_eq!(other_hosts(d.path(), "", "", NOW).len(), 0);
    }

    #[test]
    fn stale_snapshot_skipped() {
        let d = TestDir::new("stale");
        write_snapshot(d.path(), "remotes/a/snapshot.json", NOW - 6000, json!([session("s1", "NEEDS_YOU")]));
        write_status(d.path(), "a", "connected", "");
        assert_eq!(other_hosts(d.path(), "", "", NOW).len(), 0);
        // fresh -> included
        write_snapshot(d.path(), "remotes/a/snapshot.json", NOW, json!([session("s1", "NEEDS_YOU")]));
        assert_eq!(other_hosts(d.path(), "", "", NOW).len(), 1);
    }

    #[test]
    fn missing_remotes_dir_no_hosts() {
        let d = TestDir::new("no-remotes");
        assert!(other_hosts(d.path(), "", "", NOW).is_empty());
    }
}
