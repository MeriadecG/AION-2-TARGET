//! Aiguillage des segments capturés : repérage du port du jeu, filtrage, analyse.
//!
//! Adapté de `combat/capture_dispatcher.rs` et `capture/combat_port_detector.rs` d'A2Tools
//! DPS Meter (GPL-3.0). Différences : boucle synchrone (pas de tokio), pas de détection de
//! fenêtre (le verrouillage repose sur le DÉBIT de signatures du jeu, comme chez A2Tools),
//! pas d'anneau de partage ni de journal de paquets.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use a2tools::capture::captured_payload::CapturedPayload;
use a2tools::capture::stream_assembler::StreamAssembler;
use a2tools::capture::stream_processor::StreamProcessor;
use a2tools::combat::data_storage::DataStorage;
use a2tools::combat::ping_tracker::PingTracker;
use a2tools::i18n::lookup::{NpcLookup, SkillLookup};

use crate::now_ms;

/// Terminateur d'enregistrement du jeu (« 0E 00 36 » depuis juin 2026, « 06 00 36 » avant).
const SIGNATURES: [&[u8]; 2] = [&[0x0E, 0x00, 0x36], &[0x06, 0x00, 0x36]];
const LOCK_THRESHOLD: u32 = 12;          // signatures dans la fenêtre pour verrouiller un flux
const LOCK_WINDOW_MS: i64 = 3_000;
const STALE_MS: i64 = 120_000;            // plus rien d'analysé : on relâche le verrou
const LOOPBACK_GRACE_MS: i64 = 2_500;

#[derive(Default)]
struct PortDetector {
    locked_port: Option<u16>,
    locked_device: Option<String>,
    candidates: HashMap<u16, Option<String>>,
    first_candidate_ms: i64,
    last_parsed_ms: i64,
}

fn is_loopback(name: &Option<String>) -> bool {
    name.as_ref().is_some_and(|n| n.to_lowercase().contains("loopback"))
}

impl PortDetector {
    fn register(&mut self, port: u16, device: Option<&str>) {
        if self.locked_port.is_some() {
            return;
        }
        if self.first_candidate_ms == 0 {
            self.first_candidate_ms = now_ms();
        }
        self.candidates.entry(port).or_insert(device.map(str::to_string));
    }

    fn confirm(&mut self, a: u16, b: u16, device: Option<&str>) {
        if self.locked_port.is_some() {
            return;
        }
        let Some(port) = [a, b].into_iter().find(|p| self.candidates.contains_key(p)) else { return };
        let dev = device.map(str::to_string).or_else(|| self.candidates.get(&port).cloned().flatten());
        // Un réducteur de ping / VPN passe par le loopback : on lui laisse une courte avance.
        if !is_loopback(&dev) && self.first_candidate_ms != 0 && now_ms() - self.first_candidate_ms < LOOPBACK_GRACE_MS {
            return;
        }
        self.locked_port = Some(port);
        self.locked_device = dev;
        self.candidates.clear();
    }

    fn reset(&mut self) {
        *self = Self::default();
    }
}

fn contains(data: &[u8], needle: &[u8]) -> bool {
    needle.len() <= data.len() && data.windows(needle.len()).any(|w| w == needle)
}

fn looks_like_tls(d: &[u8]) -> bool {
    d.len() >= 3 && (0x14..=0x17).contains(&d[0]) && d[1] == 0x03 && d[2] <= 0x04
}

fn is_tls_record(d: &[u8]) -> bool {
    if d.len() < 5 || !looks_like_tls(d) {
        return false;
    }
    let len = u16::from_be_bytes([d[3], d[4]]) as usize;
    (1..=16_384 + 256).contains(&len)
}

pub struct Dispatcher {
    pub storage: Arc<DataStorage>,
    pub ping: Arc<PingTracker>,
    skills: Arc<SkillLookup>,
    npcs: Arc<NpcLookup>,
    dot_ids: HashSet<i32>,
    port: PortDetector,
    flows: HashMap<(u16, u16), (StreamAssembler, StreamProcessor)>,
    sig_hits: HashMap<(u16, u16), (u32, i64)>,
    seen: HashSet<(u16, u16)>,
    tls: HashSet<(u16, u16)>,
    /// Segments serveur → client du flux verrouillé (enregistrement de jeux de test).
    pub recorder: Option<Box<dyn FnMut(&CapturedPayload) + Send>>,
}

impl Dispatcher {
    pub fn new(storage: Arc<DataStorage>, skills: Arc<SkillLookup>, npcs: Arc<NpcLookup>,
               ping: Arc<PingTracker>, dot_ids: HashSet<i32>) -> Self {
        Self {
            storage, ping, skills, npcs, dot_ids,
            port: PortDetector::default(),
            flows: HashMap::new(),
            sig_hits: HashMap::new(),
            seen: HashSet::new(),
            tls: HashSet::new(),
            recorder: None,
        }
    }

    pub fn locked_port(&self) -> Option<u16> {
        self.port.locked_port
    }

    pub fn locked_device(&self) -> Option<String> {
        self.port.locked_device.clone()
    }

    pub fn reset_lock(&mut self) {
        self.port.reset();
        self.ping.reset();
        self.flows.clear();
        self.sig_hits.clear();
    }

    fn processor(&self) -> StreamProcessor {
        let mut p = StreamProcessor::new(self.storage.clone(), self.skills.clone(), self.npcs.clone());
        p.set_dot_skill_ids(self.dot_ids.clone());
        p
    }

    pub fn handle(&mut self, cap: &CapturedPayload) {
        let now = now_ms();
        if self.port.locked_port.is_some() && self.port.last_parsed_ms > 0 && now - self.port.last_parsed_ms > STALE_MS {
            self.reset_lock();
        }
        let current = self.port.locked_port;
        if let (Some(dev), Some(pdev)) = (&self.port.locked_device, cap.device_name.as_deref()) {
            if !dev.trim().eq_ignore_ascii_case(pdev.trim()) {
                return;
            }
        }
        if let Some(port) = current {
            if cap.src_port != port && cap.dst_port != port {
                return;
            }
            let before = self.ping.current_ping_ms();
            self.ping.on_packet(cap, port);
            if self.ping.current_ping_ms() != before {
                self.port.last_parsed_ms = now;
            }
            if cap.src_port != port {
                return;                               // seul le sens serveur → client est analysé
            }
        } else {
            if looks_like_tls(&cap.data) || !SIGNATURES.iter().any(|s| contains(&cap.data, s)) {
                return;
            }
        }
        let conn = (cap.src_port.min(cap.dst_port), cap.src_port.max(cap.dst_port));
        if self.seen.insert(conn) && is_tls_record(&cap.data) {
            self.tls.insert(conn);
        }
        if self.tls.contains(&conn) {
            return;
        }
        if let (Some(_), Some(rec)) = (current, self.recorder.as_mut()) {
            rec(cap);
        }
        let unlocked = current.is_none();
        if unlocked {
            self.port.register(cap.src_port, cap.device_name.as_deref());
            let slot = self.sig_hits.entry(conn).or_insert((0, now));
            if now - slot.1 > LOCK_WINDOW_MS {
                slot.0 = 0;
            }
            slot.0 += 1;
            slot.1 = now;
        }
        if !self.flows.contains_key(&conn) {
            let p = self.processor();
            self.flows.insert(conn, (StreamAssembler::new(), p));
        }
        let (asm, proc_) = self.flows.get_mut(&conn).expect("flux");
        let parsed = asm.process_chunk(&cap.data, proc_);
        if unlocked && self.sig_hits.get(&conn).map(|x| x.0).unwrap_or(0) >= LOCK_THRESHOLD {
            self.port.confirm(cap.src_port, cap.dst_port, cap.device_name.as_deref());
            if self.port.locked_port.is_some() {
                self.flows.retain(|k, _| *k == conn);
                self.sig_hits.clear();
            }
        }
        if parsed {
            self.port.last_parsed_ms = now;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tls_headers_are_recognised() {
        assert!(is_tls_record(&[0x17, 0x03, 0x03, 0x05, 0x7a, 0x88]));
        assert!(!is_tls_record(&[0x34, 0x21, 0x36, 0x01, 0x00, 0x00]));
    }

    #[test]
    fn lock_needs_a_sustained_signature_rate() {
        let storage = Arc::new(DataStorage::new());
        let mut d = Dispatcher::new(storage, Arc::new(SkillLookup::new()), Arc::new(NpcLookup::new()),
                                    Arc::new(PingTracker::new()), HashSet::new());
        let seg = |data: Vec<u8>| CapturedPayload {
            src_port: 13328, dst_port: 50349, data, device_name: Some("Loopback".into()),
            captured_at_ms: now_ms(), src_ip: None, dst_ip: None, tcp_seq: 0, tcp_ack: 0,
        };
        for _ in 0..(LOCK_THRESHOLD - 1) {
            d.handle(&seg(vec![0x01, 0x0E, 0x00, 0x36, 0x00]));
        }
        assert_eq!(d.locked_port(), None);
        d.handle(&seg(vec![0x01, 0x0E, 0x00, 0x36, 0x00]));
        assert_eq!(d.locked_port(), Some(13328));
        // Une connexion TLS du même serveur est exclue.
        let mut tls = seg(vec![0x17, 0x03, 0x03, 0x05, 0x7a, 0x88]);
        tls.dst_port = 50350;
        d.handle(&tls);
        assert!(d.tls.contains(&(13328, 50350)));
    }
}
