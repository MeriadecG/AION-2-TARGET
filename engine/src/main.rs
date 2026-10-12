//! aion-engine : moteur réseau PASSIF d'Aion Target.
//!
//! Lit une copie du trafic TCP de la machine (libpcap / Npcap, lecture seule, sans mode
//! promiscuité), repère le flux du jeu et l'analyse avec le cœur d'A2Tools DPS Meter
//! (GPL-3.0). Aucune injection, aucune écriture réseau, aucune modification du client,
//! aucun déchiffrement : le flux serveur → client du jeu n'est que compressé (LZ4).
//!
//! Communication avec l'interface Python : une ligne JSON par message sur stdout
//! (protocole versionné « v »: 1), commandes JSON par ligne sur stdin.
//!
//!   aion-engine                      capture en direct
//!   aion-engine --replay F.txt       rejoue une capture enregistrée (tests reproductibles)
//!   aion-engine --record F.txt       capture en direct ET enregistre le flux du jeu
//!   aion-engine --check              vérifie seulement libpcap / Npcap et les interfaces

mod dispatch;
mod kills;
mod pcap;

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use a2tools::capture::captured_payload::CapturedPayload;
use a2tools::capture::packet_accumulator::PacketAccumulator;
use a2tools::capture::stream_processor::StreamProcessor;
use a2tools::combat::data_storage::DataStorage;
use a2tools::combat::dps_calculator::DpsCalculator;
use a2tools::combat::ping_tracker::{PerfClock, PingTracker};
use a2tools::i18n::lookup::{NpcLookup, SkillLookup};
use serde_json::{json, Value};

pub const PROTOCOL: u32 = 1;
pub const PARSER: &str = "a2tools@71868460c0ee";

pub fn now_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as i64
}

fn emit(v: Value) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{v}");
    let _ = out.flush();
}

fn log(msg: impl Into<String>) {
    emit(json!({"v": PROTOCOL, "type": "log", "msg": msg.into()}));
}

// --- horloge du ping (compteur de performance, comme le client du jeu) -------------------

#[cfg(windows)]
fn perf_read() -> (i64, i64) {
    unsafe extern "system" {
        fn QueryPerformanceCounter(c: *mut i64) -> i32;
        fn QueryPerformanceFrequency(f: *mut i64) -> i32;
    }
    let (mut c, mut f) = (0i64, 0i64);
    unsafe {
        QueryPerformanceFrequency(&mut f);
        QueryPerformanceCounter(&mut c);
    }
    ((c as i128 * 1000 / f.max(1) as i128) as i64, now_ms())
}

#[cfg(unix)]
fn perf_read() -> (i64, i64) {
    #[repr(C)]
    struct Ts {
        s: i64,
        ns: i64,
    }
    unsafe extern "C" {
        fn clock_gettime(id: i32, tp: *mut Ts) -> i32;
    }
    let mut ts = Ts { s: 0, ns: 0 };
    unsafe { clock_gettime(4, &mut ts) }; // CLOCK_MONOTONIC_RAW
    (ts.s * 1000 + ts.ns / 1_000_000, now_ms())
}

fn perf_clock() -> Option<PerfClock> {
    Some(perf_read)
}

// --- données de jeu (A2Tools, embarquées) ------------------------------------------------

struct GameData {
    skills: Arc<SkillLookup>,
    npcs: Arc<NpcLookup>,
    dot_ids: HashSet<i32>,
    servers: HashMap<u16, String>,
}

fn game_data() -> GameData {
    let skills = Arc::new(SkillLookup::new());
    skills.load_from_json(include_str!("../data/skills_fr.json"));
    let npcs = Arc::new(NpcLookup::new());
    npcs.load_from_json(include_str!("../data/npcs_fr.json"));
    let dot_ids = serde_json::from_str::<Vec<i32>>(include_str!("../data/dot_skill_ids.json"))
        .unwrap_or_default()
        .into_iter()
        .collect();
    let mut servers = HashMap::new();
    if let Ok(v) = serde_json::from_str::<Value>(include_str!("../data/servers.json")) {
        if let Some(map) = v.get("servers").and_then(Value::as_object) {
            for (id, names) in map {
                if let (Ok(id), Some(name)) = (id.parse::<u16>(), names.get("en").and_then(Value::as_str)) {
                    servers.insert(id, name.to_string());
                }
            }
        }
    }
    GameData { skills, npcs, dot_ids, servers }
}

// --- état publié ---------------------------------------------------------------------------

struct Publisher {
    seen_players: HashSet<(String, u16)>,
    last_party: String,
    fights_sent: HashSet<String>,
}

impl Publisher {
    fn new() -> Self {
        Self { seen_players: HashSet::new(), last_party: String::new(), fights_sent: HashSet::new() }
    }

    fn server_name(gd: &GameData, id: u16) -> Value {
        gd.servers.get(&id).map(|s| Value::String(s.clone())).unwrap_or(Value::Null)
    }

    fn dps(&self, calc: &mut DpsCalculator, ping: &PingTracker) {
        let d = calc.get_dps();
        let total: f64 = d.map.values().map(|p| p.amount).sum();
        let mut rows: Vec<Value> = d
            .map
            .iter()
            .filter(|(_, p)| p.amount > 0.0)
            .map(|(id, p)| {
                json!({
                    "id": id, "name": p.nickname, "job": p.job, "dps": p.dps.round(),
                    "damage": p.amount.round(), "share": if total > 0.0 { p.amount / total } else { 0.0 },
                    "cp": p.combat_power, "me": d.local_player_id == Some(*id as i64),
                })
            })
            .collect();
        rows.sort_by(|a, b| b["damage"].as_f64().partial_cmp(&a["damage"].as_f64()).unwrap_or(std::cmp::Ordering::Equal));
        emit(json!({
            "v": PROTOCOL, "type": "dps", "t": now_ms(),
            "target": {"id": d.target_id, "name": d.target_name, "boss": d.target_is_boss,
                       "max_hp": d.target_max_hp, "hp": d.target_current_hp, "mode": d.target_mode},
            "battle_ms": d.battle_time, "rows": rows, "total": total.round(),
            "ping_ms": ping.current_ping_ms(),
        }));
    }

    fn players(&mut self, storage: &DataStorage, gd: &GameData) {
        let mut new = Vec::new();
        for (id, name) in storage.get_nicknames() {
            if name.is_empty() || name.starts_with('#') {
                continue;
            }
            let server = storage.player_server(&name);
            if self.seen_players.insert((name.clone(), server)) {
                new.push(json!({"id": id, "name": name, "server_id": server,
                                "server": Self::server_name(gd, server), "t": now_ms()}));
            }
        }
        if !new.is_empty() {
            emit(json!({"v": PROTOCOL, "type": "players", "observed": new,
                        "note": "personnages observés (vus à l'écran), pas la liste complète de l'instance"}));
        }
    }

    fn party(&mut self, storage: &DataStorage, gd: &GameData) {
        let mut members: Vec<Value> = storage
            .get_party_members()
            .into_iter()
            .map(|(name, m)| json!({
                "name": name, "slot": m.slot, "level": m.level, "gs": m.gear_score, "cp": m.combat_power,
                "server_id": m.server_id, "server": Self::server_name(gd, m.server_id), "dbid": m.dbid.to_string(),
            }))
            .collect();
        members.sort_by_key(|m| m["slot"].as_u64());
        let key = serde_json::to_string(&members).unwrap_or_default();
        if key != self.last_party {
            self.last_party = key;
            emit(json!({"v": PROTOCOL, "type": "party", "members": members}));
        }
    }

    fn fights(&mut self, calc: &mut DpsCalculator) {
        for rec in calc.snapshot_boss_fights() {
            if self.fights_sent.insert(rec.id.clone()) {
                if let Ok(v) = serde_json::to_value(&rec) {
                    emit(json!({"v": PROTOCOL, "type": "fight", "record": v}));
                }
            }
        }
    }
}

// --- rejeu d'une capture enregistrée -------------------------------------------------------

fn parse_ts(ts: &str) -> Option<i64> {
    ts.parse::<i64>().ok().or_else(|| {
        chrono::DateTime::parse_from_rfc3339(ts).ok().map(|d| d.timestamp_millis())
    }).or_else(|| {
        chrono::NaiveDateTime::parse_from_str(ts, "%Y-%m-%dT%H:%M:%S%.f").ok().map(|d| d.and_utc().timestamp_millis())
    })
}

fn decode_hex(s: &str) -> Option<Vec<u8>> {
    let s = s.trim();
    if s.len() % 2 != 0 {
        return None;
    }
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok()).collect()
}

fn replay(path: &str, gd: &GameData, mode: &str) -> Result<(), String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{path} : {e}"))?;
    let storage = Arc::new(DataStorage::new());
    let ping = Arc::new(PingTracker::new());
    let mut proc_ = StreamProcessor::new(storage.clone(), gd.skills.clone(), gd.npcs.clone());
    proc_.set_dot_skill_ids(gd.dot_ids.clone());
    let mut calc = DpsCalculator::new(storage.clone(), gd.skills.clone(), gd.npcs.clone(), ping.clone());
    calc.set_target_selection_mode(mode);
    let mut streams: HashMap<String, PacketAccumulator> = HashMap::new();
    let mut publisher = Publisher::new();
    let (mut lines, mut bad) = (0usize, 0usize);
    for line in text.lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut parts = line.splitn(3, '|');
        let (Some(ts), Some(key), Some(hex)) = (parts.next(), parts.next(), parts.next()) else {
            bad += 1;
            continue;
        };
        let Some(bytes) = decode_hex(hex) else {
            bad += 1;
            continue;
        };
        lines += 1;
        proc_.set_override_timestamp(parse_ts(ts));
        let acc = streams.entry(key.to_string()).or_insert_with(PacketAccumulator::new);
        acc.append(&bytes);
        let consumed = proc_.consume_stream(acc.snapshot());
        if consumed > 0 {
            acc.discard_bytes(consumed);
        }
    }
    proc_.set_override_timestamp(None);
    publisher.players(&storage, gd);
    publisher.party(&storage, gd);
    for ev in kills::KillTracker::new().scan(&storage, &gd.npcs, now_ms()) {
        emit(ev);
    }
    calc.set_every_fight(true);
    publisher.fights(&mut calc);
    publisher.dps(&mut calc, &ping);
    emit(json!({"v": PROTOCOL, "type": "replay_done", "segments": lines, "ignored": bad}));
    Ok(())
}

// --- capture en direct ---------------------------------------------------------------------

fn live(gd: GameData, record: Option<String>, mode: &str) -> Result<(), String> {
    let lib = Arc::new(pcap::PcapLib::load().map_err(|e| {
        emit(json!({"v": PROTOCOL, "type": "status", "capture": "missing_library", "detail": e}));
        e
    })?);
    let (tx, rx) = mpsc::sync_channel::<CapturedPayload>(8192);
    let running = Arc::new(AtomicBool::new(true));
    let devices = pcap::start(lib, tx, running.clone(), |m| log(m)).map_err(|e| {
        emit(json!({"v": PROTOCOL, "type": "status", "capture": "no_permission", "detail": e}));
        e
    })?;
    emit(json!({"v": PROTOCOL, "type": "status", "capture": "running", "devices": devices}));

    let storage = Arc::new(DataStorage::new());
    let ping = Arc::new(PingTracker::with_perf_clock(perf_clock()));
    let mut disp = dispatch::Dispatcher::new(storage.clone(), gd.skills.clone(), gd.npcs.clone(),
                                             ping.clone(), gd.dot_ids.clone());
    if let Some(path) = record {
        let mut file = std::io::BufWriter::new(std::fs::File::create(&path).map_err(|e| e.to_string())?);
        disp.recorder = Some(Box::new(move |cap: &CapturedPayload| {
            let ts = chrono::DateTime::from_timestamp_millis(cap.captured_at_ms)
                .map(|d| d.format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string())
                .unwrap_or_default();
            let hex: String = cap.data.iter().map(|b| format!("{b:02x}")).collect();
            let _ = writeln!(file, "{ts}|Client:{}:{}|{hex}", cap.dst_port, cap.src_port);
            let _ = file.flush();
        }));
        log(format!("enregistrement du flux du jeu dans {path}"));
    }
    let mut calc = DpsCalculator::new(storage.clone(), gd.skills.clone(), gd.npcs.clone(), ping.clone());
    calc.set_target_selection_mode(mode);

    // Commandes (stdin) : un thread, un canal.
    let (ctx, crx) = mpsc::channel::<Value>();
    std::thread::spawn(move || {
        for line in std::io::stdin().lock().lines() {
            let Ok(line) = line else { break };
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if ctx.send(v).is_err() {
                    break;
                }
            }
        }
        // stdin fermé = l'interface est partie : on s'arrête.
        let _ = ctx.send(json!({"cmd": "quit"}));
    });

    let mut publisher = Publisher::new();
    let mut kill_tracker = kills::KillTracker::new();
    // Mode « observation légère » : l'interface peut couper l'envoi du DPS (préchargement seul).
    let mut publish_dps = true;
    let (mut last_dps, mut last_slow, mut last_players) = (Instant::now(), Instant::now(), Instant::now());
    let mut last_lock: Option<u16> = None;
    loop {
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(cap) => disp.handle(&cap),
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
        while let Ok(cmd) = crx.try_recv() {
            match cmd.get("cmd").and_then(Value::as_str) {
                Some("quit") => {
                    running.store(false, Ordering::Relaxed);
                    return Ok(());
                }
                Some("reset") => {
                    publisher.fights(&mut calc);
                    calc.restart_target_selection(true);
                    storage.forget_guessed_nicknames();
                    storage.hide_party_placeholders();
                }
                Some("relock") => disp.reset_lock(),
                Some("publish_dps") => {
                    publish_dps = cmd.get("value").and_then(Value::as_bool).unwrap_or(true);
                }
                Some("mode") => {
                    if let Some(m) = cmd.get("value").and_then(Value::as_str) {
                        calc.set_target_selection_mode(m);
                    }
                }
                _ => {}
            }
        }
        if disp.locked_port() != last_lock {
            last_lock = disp.locked_port();
            emit(json!({"v": PROTOCOL, "type": "status", "capture": "running",
                        "game_port": last_lock, "device": disp.locked_device()}));
        }
        if last_dps.elapsed() >= Duration::from_millis(500) {
            last_dps = Instant::now();
            if last_lock.is_some() && publish_dps {
                publisher.dps(&mut calc, &ping);
            }
        }
        // Personnages observés : publiés vite (préchargement avant qu'on les cible) ; seuls les
        // NOUVEAUX sont envoyés, le coût est négligeable.
        if last_players.elapsed() >= Duration::from_millis(250) {
            last_players = Instant::now();
            publisher.players(&storage, &gd);
            // Morts de monstres en combat (familiers) : une seule fois chacune.
            for ev in kill_tracker.scan(&storage, &gd.npcs, now_ms()) {
                emit(ev);
            }
        }
        if last_slow.elapsed() >= Duration::from_secs(2) {
            last_slow = Instant::now();
            publisher.party(&storage, &gd);
            publisher.fights(&mut calc);
        }
    }
    Ok(())
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let arg = |name: &str| args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned();
    emit(json!({"v": PROTOCOL, "type": "hello", "engine": env!("CARGO_PKG_VERSION"), "parser": PARSER,
                "features": ["publish_dps", "kills", "players_ts"]}));
    if args.iter().any(|a| a == "--check") {
        match pcap::PcapLib::load().and_then(|l| l.devices()) {
            Ok(devs) => emit(json!({"v": PROTOCOL, "type": "check", "ok": true,
                                    "devices": devs.iter().filter(|d| d.has_addresses && !pcap::skip_device(&d.name))
                                        .map(|d| d.label().to_string()).collect::<Vec<_>>()})),
            Err(e) => emit(json!({"v": PROTOCOL, "type": "check", "ok": false, "detail": e})),
        }
        return;
    }
    let gd = game_data();
    let mode = arg("--mode").unwrap_or_else(|| "bossTargets".into());
    let result = if let Some(path) = arg("--replay") { replay(&path, &gd, &mode) } else { live(gd, arg("--record"), &mode) };
    if let Err(e) = result {
        emit(json!({"v": PROTOCOL, "type": "error", "detail": e}));
        std::process::exit(2);
    }
}
