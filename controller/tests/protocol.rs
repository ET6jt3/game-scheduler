//! Protocol conformance against the REAL binary (NC6): spawn
//! `controller --dry-run --protocol` and assert the stdout stream is a
//! well-formed NC6 session (HELLO first, READY, RESULT last, every line
//! schema-valid). Uses the synthetic backend + probe window: no game, no
//! input, no model.

#![cfg(windows)]

use std::process::{Command, Stdio};

#[test]
fn protocol_session_stream_conforms() {
    if !controller::window::interactive_desktop_available() {
        eprintln!("skipped: no interactive desktop (service context)");
        return;
    }
    let exe = env!("CARGO_BIN_EXE_controller");
    let mut child = Command::new(exe)
        .args([
            "--dry-run",
            "--protocol",
            "--window",
            "@probe",
            "--backend",
            "synthetic",
            "--duration",
            "1",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn controller");
    let stdout = child.stdout.take().expect("stdout");
    let status = child.wait().expect("wait");
    assert!(status.success(), "controller exited {status:?}");

    let mut reader = std::io::BufReader::new(stdout);
    let mut lines = Vec::new();
    loop {
        let mut line = String::new();
        match std::io::BufRead::read_line(&mut reader, &mut line) {
            Ok(0) => break,
            Ok(_) => {
                if line.trim().is_empty() {
                    continue;
                }
                lines.push(line);
            }
            Err(e) => panic!("read stdout: {e}"),
        }
    }
    assert!(
        lines.len() >= 3,
        "expected HELLO+READY+RESULT, got {lines:?}"
    );

    let mut saw_ready = false;
    for (i, line) in lines.iter().enumerate() {
        let msg = controller::protocol::Envelope::parse(line)
            .unwrap_or_else(|e| panic!("line {i} violates the schema: {e} — {line}"));
        if i == 0 {
            assert!(
                matches!(msg.payload, controller::protocol::Payload::Hello(_)),
                "first line must be HELLO: {line}"
            );
        }
        if matches!(msg.payload, controller::protocol::Payload::Ready(_)) {
            saw_ready = true;
        }
    }
    assert!(saw_ready, "READY missing");
    let last = lines.last().expect("non-empty");
    let msg = controller::protocol::Envelope::parse(last).expect("last line");
    match msg.payload {
        controller::protocol::Payload::Result(r) => {
            assert_eq!(r.outcome, controller::protocol::Outcome::Done);
        }
        other => panic!("last line must be RESULT, got {other:?}"),
    }
}

/// D1 gate regression: a skill that reaches its terminal state must emit
/// EXACTLY ONE EVENT for that terminal transition, even though the engine
/// keeps returning Done on every later cycle (the 300s soak caught this
/// flooding before the gate existed; the transition-into-terminal arm also
/// had to join the latch — it used to emit and then let Done re-emit).
/// Drives the real probe window scene: a probe watching the normalized
/// (0.6,0.6)-(0.8,0.8) red anchor fires within the first cycles, so the
/// skill walks s0 -> done; if the probe never fires (load transient), the
/// 2s timeout retreats into the same terminal state — either way the
/// terminal state is announced EXACTLY once.
#[test]
fn terminal_skill_event_emits_once() {
    use std::io::Read;
    if !controller::window::interactive_desktop_available() {
        eprintln!("skipped: no interactive desktop (service context)");
        return;
    }
    let dir = std::env::temp_dir().join(format!("nf_proto_gate_{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("temp dir");
    let probes = dir.join("probes.json");
    let skill = dir.join("skill.json");
    std::fs::write(
        &probes,
        r#"[{"name":"rect_probe","x":384,"y":288,"w":128,"h":96,"expected":[16,40,200],"tolerance":30,"min_fraction":0.4,"step":4}]"#,
    )
    .expect("write probes");
    std::fs::write(
        &skill,
        r#"{"name":"gate_test","start":"s0","states":[
            {"name":"s0","expect":[{"probe":"rect_probe"}],"actions":["click"],"next":"done","timeout_ms":2000,"max_retries":1,"fallback":"done","terminal":false},
            {"name":"done","expect":[],"actions":[],"next":"done","timeout_ms":1000,"max_retries":1,"terminal":true}]}"#,
    )
    .expect("write skill");

    let exe = env!("CARGO_BIN_EXE_controller");
    let mut child = Command::new(exe)
        .args([
            "--dry-run",
            "--protocol",
            "--window",
            "@probe",
            "--backend",
            "gdi",
            "--probes",
        ])
        .arg(probes.to_string_lossy().as_ref())
        .arg("--skill")
        .arg(skill.to_string_lossy().as_ref())
        .arg("--duration")
        .arg("6")
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn controller");
    let mut stdout = String::new();
    child
        .stdout
        .take()
        .expect("stdout")
        .read_to_string(&mut stdout)
        .expect("read");
    let status = child.wait().expect("wait");
    assert!(status.success(), "controller exited {status:?}");
    let _ = std::fs::remove_dir_all(&dir);

    let mut done_events = 0;
    let mut transitions = 0;
    let mut last_was_result_done = false;
    for line in stdout.lines().filter(|l| !l.trim().is_empty()) {
        let msg = controller::protocol::Envelope::parse(line)
            .unwrap_or_else(|e| panic!("protocol violation: {e} — {line}"));
        match msg.payload {
            controller::protocol::Payload::Event(e) => {
                if e.state == "done" {
                    done_events += 1;
                }
                transitions += 1;
            }
            controller::protocol::Payload::Result(r) => {
                last_was_result_done = r.outcome == controller::protocol::Outcome::Done;
            }
            _ => {}
        }
    }
    assert!(
        last_was_result_done,
        "the probe fires on the anchored rect, so the skill must complete"
    );
    assert_eq!(
        done_events, 1,
        "terminal 'done' event must be emitted exactly once (D1)"
    );
    assert_eq!(
        transitions, 1,
        "exactly one state change is announced (s0 -> done, by probe or \
         by terminal fallback); later Done cycles stay silent"
    );
}

/// D1 gate on the FAILURE path: a skill whose expectations can never be met
/// (a label no detector emits) must announce engine failure EXACTLY once —
/// the synthetic backend keeps returning the same detection every cycle, so
/// without the latch the engine's repeated Failed outcomes would flood the
/// stream the same way Done used to.
#[test]
fn failed_skill_event_emits_once() {
    use std::io::Read;
    if !controller::window::interactive_desktop_available() {
        eprintln!("skipped: no interactive desktop (service context)");
        return;
    }
    let dir = std::env::temp_dir().join(format!("nf_proto_fail_{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("temp dir");
    let skill = dir.join("skill.json");
    std::fs::write(
        &skill,
        r#"{"name":"doomed","start":"s0","states":[
            {"name":"s0","expect":[{"label":"never_detected"}],"actions":[],"next":"done","timeout_ms":1500,"max_retries":1,"terminal":false},
            {"name":"done","expect":[],"actions":[],"next":"done","timeout_ms":1000,"max_retries":1,"terminal":true}]}"#,
    )
    .expect("write skill");

    let exe = env!("CARGO_BIN_EXE_controller");
    let mut child = Command::new(exe)
        .args([
            "--dry-run",
            "--protocol",
            "--window",
            "@probe",
            "--backend",
            "synthetic",
            "--skill",
        ])
        .arg(skill.to_string_lossy().as_ref())
        .arg("--duration")
        .arg("4")
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn controller");
    let mut stdout = String::new();
    child
        .stdout
        .take()
        .expect("stdout")
        .read_to_string(&mut stdout)
        .expect("read");
    let status = child.wait().expect("wait");
    assert!(status.success(), "controller exited {status:?}");
    let _ = std::fs::remove_dir_all(&dir);

    let mut failed_events = 0;
    let mut events = 0;
    let mut result_seen: Option<controller::protocol::ResultPayload> = None;
    for line in stdout.lines().filter(|l| !l.trim().is_empty()) {
        let msg = controller::protocol::Envelope::parse(line)
            .unwrap_or_else(|e| panic!("protocol violation: {e} — {line}"));
        match msg.payload {
            controller::protocol::Payload::Event(e) => {
                events += 1;
                if e.state == "failed" {
                    failed_events += 1;
                }
            }
            controller::protocol::Payload::Result(r) => {
                result_seen = Some(r);
            }
            _ => {}
        }
    }
    // 2026-09-13/14 night revision: a terminally failed skill is a FAILED
    // run. The scheduler's retries / failure notify / failure screenshots /
    // feedback rollup all key off Execution status, so the RESULT must not
    // report Done just because the session itself ran cleanly to its term.
    // (Previously this test pinned outcome=done — that let a stuck walk
    // surface as a green execution in the dashboard.)
    let r = result_seen.expect("RESULT line");
    assert_eq!(
        r.outcome,
        controller::protocol::Outcome::Failed,
        "a doomed skill must surface as a failed run"
    );
    assert!(
        r.error
            .as_deref()
            .is_some_and(|e| e.contains("skill failed in state")),
        "RESULT error must name the skill failure, got {:?}",
        r.error
    );
    assert_eq!(
        failed_events, 1,
        "engine failure must be announced exactly once (D1)"
    );
    assert_eq!(events, 1, "the failed skill emits no other state changes");
}

/// `--on-terminal stop`: a skill that fails terminally should end the session
/// PROMPTLY instead of cycling until the duration budget runs out — the
/// scheduler's Execution row stays "running" until RESULT, so every wasted
/// minute delays retries, failure notify and feedback stats. Fixture: a
/// doomed skill (300ms timeout, no retries) inside a 30s session; with the
/// flag the session must end in a small fraction of that budget.
#[test]
fn on_terminal_stop_ends_the_failed_session_early() {
    use std::io::Read;
    use std::time::Instant;
    if !controller::window::interactive_desktop_available() {
        eprintln!("skipped: no interactive desktop (service context)");
        return;
    }
    let dir = std::env::temp_dir().join(format!("nf_proto_stop_{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("temp dir");
    let skill = dir.join("skill.json");
    std::fs::write(
        &skill,
        r#"{"name":"doomed","start":"s0","states":[
            {"name":"s0","expect":[{"label":"never_detected"}],"actions":[],"next":"done","timeout_ms":300,"max_retries":0,"terminal":false},
            {"name":"done","expect":[],"actions":[],"next":"done","timeout_ms":1000,"max_retries":1,"terminal":true}]}"#,
    )
    .expect("write skill");

    let exe = env!("CARGO_BIN_EXE_controller");
    let started = Instant::now();
    let mut child = Command::new(exe)
        .args([
            "--dry-run",
            "--protocol",
            "--window",
            "@probe",
            "--backend",
            "synthetic",
            "--skill",
        ])
        .arg(skill.to_string_lossy().as_ref())
        .args(["--duration", "30", "--on-terminal", "stop"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn controller");
    let mut stdout = String::new();
    child
        .stdout
        .take()
        .expect("stdout")
        .read_to_string(&mut stdout)
        .expect("read");
    let status = child.wait().expect("wait");
    let elapsed = started.elapsed();
    assert!(status.success(), "controller exited {status:?}");
    let _ = std::fs::remove_dir_all(&dir);

    assert!(
        elapsed.as_secs() < 25,
        "the failed walk must end the session well before the 30s budget, took {elapsed:?}"
    );
    let mut failed_events = 0;
    let mut outcome = None;
    for line in stdout.lines().filter(|l| !l.trim().is_empty()) {
        let msg = controller::protocol::Envelope::parse(line)
            .unwrap_or_else(|e| panic!("protocol violation: {e} — {line}"));
        match msg.payload {
            controller::protocol::Payload::Event(e) => {
                if e.state == "failed" {
                    failed_events += 1;
                }
            }
            controller::protocol::Payload::Result(r) => outcome = Some(r),
            _ => {}
        }
    }
    let r = outcome.expect("RESULT line");
    assert_eq!(r.outcome, controller::protocol::Outcome::Failed);
    assert_eq!(failed_events, 1, "terminal failed EVENT stays exactly-once");
}

/// Default (no --on-terminal) must stay observe-to-duration: the failed walk
/// keeps cycling until the requested duration, then RESULT failed. This pins
/// the semantics every existing soak ran under, so the new flag can never
/// silently change the default.
#[test]
fn default_continues_after_failure_until_duration() {
    use std::io::Read;
    use std::time::Instant;
    if !controller::window::interactive_desktop_available() {
        eprintln!("skipped: no interactive desktop (service context)");
        return;
    }
    let dir = std::env::temp_dir().join(format!("nf_proto_cont_{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("temp dir");
    let skill = dir.join("skill.json");
    std::fs::write(
        &skill,
        r#"{"name":"doomed","start":"s0","states":[
            {"name":"s0","expect":[{"label":"never_detected"}],"actions":[],"next":"done","timeout_ms":300,"max_retries":0,"terminal":false},
            {"name":"done","expect":[],"actions":[],"next":"done","timeout_ms":1000,"max_retries":1,"terminal":true}]}"#,
    )
    .expect("write skill");

    let exe = env!("CARGO_BIN_EXE_controller");
    let started = Instant::now();
    let mut child = Command::new(exe)
        .args([
            "--dry-run",
            "--protocol",
            "--window",
            "@probe",
            "--backend",
            "synthetic",
            "--skill",
        ])
        .arg(skill.to_string_lossy().as_ref())
        .arg("--duration")
        .arg("2")
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn controller");
    let mut stdout = String::new();
    child
        .stdout
        .take()
        .expect("stdout")
        .read_to_string(&mut stdout)
        .expect("read");
    let status = child.wait().expect("wait");
    let elapsed = started.elapsed();
    assert!(status.success(), "controller exited {status:?}");
    let _ = std::fs::remove_dir_all(&dir);

    assert!(
        elapsed.as_secs_f32() >= 1.9,
        "without the flag the session must run to its duration, ended after {elapsed:?}"
    );
    let mut outcome = None;
    for line in stdout.lines().filter(|l| !l.trim().is_empty()) {
        let msg = controller::protocol::Envelope::parse(line)
            .unwrap_or_else(|e| panic!("protocol violation: {e} — {line}"));
        if let controller::protocol::Payload::Result(r) = msg.payload {
            outcome = Some(r);
        }
    }
    let r = outcome.expect("RESULT line");
    assert_eq!(r.outcome, controller::protocol::Outcome::Failed);
}
