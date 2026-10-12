//! Capture passive (lecture seule) du trafic TCP de la machine, via libpcap / Npcap chargés
//! à l'exécution (aucun SDK à la compilation, aucune dépendance si le module DPS est éteint).
//!
//! Adapté de `capture/pcap_capturer.rs` et `platform/*/pcap.rs` d'A2Tools DPS Meter
//! (GPL-3.0, © taengu et contributeurs) : mêmes structures FFI et même lecture des en-têtes.
//! Modifications : threads std + canal std, pas de tokio ni de tracing, messages en français.

use std::ffi::{c_char, c_int, c_long, c_uint, CStr, CString};
use std::ptr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::SyncSender;
use std::sync::Arc;

use a2tools::capture::captured_payload::CapturedPayload;
use libloading::Library;

use crate::now_ms;

type PcapT = *mut std::ffi::c_void;
type PcapIfT = *mut PcapIf;

#[repr(C)]
struct PcapIf {
    next: *mut PcapIf,
    name: *const c_char,
    description: *const c_char,
    addresses: *mut std::ffi::c_void,
    flags: c_uint,
}

/// `struct pcap_pkthdr` : horodatage `timeval` (deux `long`), puis longueurs.
#[repr(C)]
struct PcapPkthdr {
    ts_sec: c_long,
    ts_usec: c_long,
    caplen: c_uint,
    len: c_uint,
}

const PCAP_IF_LOOPBACK: c_uint = 0x0000_0001;

#[cfg(windows)]
pub const LIBRARIES: &[&str] = &["wpcap.dll"];
#[cfg(not(windows))]
pub const LIBRARIES: &[&str] = &["libpcap.so.1", "libpcap.so.0.8", "libpcap.so", "libpcap.dylib"];

#[cfg(windows)]
pub const MISSING_HELP: &str = "Npcap n'est pas installé : télécharge-le sur https://npcap.com puis relance.";
#[cfg(not(windows))]
pub const MISSING_HELP: &str =
    "libpcap introuvable (Fedora : sudo dnf install libpcap ; Debian : sudo apt install libpcap0.8).";

#[derive(Clone, Debug)]
pub struct Device {
    pub name: String,
    pub description: String,
    pub has_addresses: bool,
    pub is_loopback: bool,
}

impl Device {
    pub fn label(&self) -> &str {
        if self.description.is_empty() { &self.name } else { &self.description }
    }
}

/// Pseudo-interfaces Linux inutiles (« any » doublerait chaque paquet, ponts de conteneurs…).
pub fn skip_device(name: &str) -> bool {
    name == "any"
        || ["nflog", "nfqueue", "usbmon", "dbus", "bluetooth", "veth", "docker", "br-", "podman"]
            .iter()
            .any(|p| name.starts_with(p))
}

pub struct PcapLib {
    _lib: Library,
    findalldevs: unsafe extern "C" fn(*mut PcapIfT, *mut c_char) -> c_int,
    freealldevs: unsafe extern "C" fn(PcapIfT),
    open_live: unsafe extern "C" fn(*const c_char, c_int, c_int, c_int, *mut c_char) -> PcapT,
    close: unsafe extern "C" fn(PcapT),
    next_ex: unsafe extern "C" fn(PcapT, *mut *mut PcapPkthdr, *mut *const u8) -> c_int,
    datalink: unsafe extern "C" fn(PcapT) -> c_int,
}

unsafe impl Send for PcapLib {}
unsafe impl Sync for PcapLib {}

#[cfg(windows)]
fn prepare() {
    // Npcap installe wpcap.dll dans System32\Npcap : dossier ajouté à la recherche des DLL.
    use std::os::windows::ffi::OsStrExt;
    unsafe extern "system" {
        fn SetDllDirectoryW(path: *const u16) -> i32;
    }
    let root = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
    let folder = std::path::Path::new(&root).join("System32").join("Npcap");
    let wide: Vec<u16> = folder.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
    unsafe { SetDllDirectoryW(wide.as_ptr()) };
}

#[cfg(not(windows))]
fn prepare() {}

impl PcapLib {
    pub fn load() -> Result<Self, String> {
        prepare();
        let mut errors = Vec::new();
        let mut loaded = None;
        for name in LIBRARIES {
            match unsafe { Library::new(name) } {
                Ok(lib) => {
                    loaded = Some(lib);
                    break;
                }
                Err(e) => errors.push(format!("{name}: {e}")),
            }
        }
        let lib = loaded.ok_or_else(|| format!("{MISSING_HELP} ({})", errors.join(" ; ")))?;
        unsafe {
            macro_rules! sym {
                ($name:literal) => {
                    *lib.get($name).map_err(|e| format!("{}: {e}", String::from_utf8_lossy($name)))?
                };
            }
            Ok(Self {
                findalldevs: sym!(b"pcap_findalldevs"),
                freealldevs: sym!(b"pcap_freealldevs"),
                open_live: sym!(b"pcap_open_live"),
                close: sym!(b"pcap_close"),
                next_ex: sym!(b"pcap_next_ex"),
                datalink: sym!(b"pcap_datalink"),
                _lib: lib,
            })
        }
    }

    pub fn devices(&self) -> Result<Vec<Device>, String> {
        let mut all: PcapIfT = ptr::null_mut();
        let mut errbuf = [0u8; 256];
        let ret = unsafe { (self.findalldevs)(&mut all, errbuf.as_mut_ptr() as *mut c_char) };
        if ret != 0 || all.is_null() {
            let err = unsafe { CStr::from_ptr(errbuf.as_ptr() as *const c_char) }.to_string_lossy().to_string();
            return Err(format!("liste des interfaces impossible : {err}"));
        }
        let mut out = Vec::new();
        let mut dev = all;
        while !dev.is_null() {
            let d = unsafe { &*dev };
            let s = |p: *const c_char| {
                if p.is_null() { String::new() } else { unsafe { CStr::from_ptr(p) }.to_string_lossy().to_string() }
            };
            out.push(Device {
                name: s(d.name),
                description: s(d.description),
                has_addresses: !d.addresses.is_null(),
                is_loopback: d.flags & PCAP_IF_LOOPBACK != 0,
            });
            dev = d.next;
        }
        unsafe { (self.freealldevs)(all) };
        Ok(out)
    }

    fn open(&self, name: &str) -> Result<PcapT, String> {
        let c_name = CString::new(name).map_err(|e| e.to_string())?;
        let mut errbuf = [0u8; 256];
        // snaplen 65535, PAS de mode promiscuité (seulement le trafic de cette machine), 100 ms.
        let h = unsafe { (self.open_live)(c_name.as_ptr(), 65535, 0, 100, errbuf.as_mut_ptr() as *mut c_char) };
        if h.is_null() {
            let err = unsafe { CStr::from_ptr(errbuf.as_ptr() as *const c_char) }.to_string_lossy().to_string();
            return Err(err);
        }
        Ok(h)
    }
}

/// Lance un thread de capture par interface utile. Renvoie les interfaces ouvertes.
pub fn start(lib: Arc<PcapLib>, tx: SyncSender<CapturedPayload>, running: Arc<AtomicBool>,
             log: impl Fn(String) + Send + Sync + Clone + 'static) -> Result<Vec<String>, String> {
    let devices: Vec<Device> = lib
        .devices()?
        .into_iter()
        .filter(|d| d.has_addresses && !skip_device(&d.name))
        .collect();
    if devices.is_empty() {
        return Err("aucune interface réseau exploitable".into());
    }
    let mut opened = Vec::new();
    for dev in devices {
        let handle = match lib.open(&dev.name) {
            Ok(h) => h,
            Err(e) => {
                log(format!("interface {} : ouverture impossible ({e})", dev.label()));
                continue;
            }
        };
        opened.push(dev.label().to_string());
        let lib = lib.clone();
        let tx = tx.clone();
        let running = running.clone();
        let label = dev.label().to_string();
        let handle_addr = handle as usize;
        std::thread::Builder::new()
            .name(format!("pcap-{label}"))
            .spawn(move || {
                let handle = handle_addr as PcapT;
                let link = unsafe { (lib.datalink)(handle) };
                while running.load(Ordering::Relaxed) {
                    let mut hdr: *mut PcapPkthdr = ptr::null_mut();
                    let mut data: *const u8 = ptr::null();
                    match unsafe { (lib.next_ex)(handle, &mut hdr, &mut data) } {
                        1 => {
                            let h = unsafe { &*hdr };
                            let frame = unsafe { std::slice::from_raw_parts(data, h.caplen as usize) };
                            let ts = (h.ts_sec as i64) * 1000 + (h.ts_usec as i64) / 1000;
                            if let Some(mut p) = parse_tcp_payload(frame, link, &label) {
                                p.captured_at_ms = if (1_000_000_000_000..2_000_000_000_000).contains(&ts) { ts } else { now_ms() };
                                let _ = tx.try_send(p);          // file pleine : on jette (jamais de blocage)
                            }
                        }
                        0 => continue,
                        _ => break,
                    }
                }
                unsafe { (lib.close)(handle) };
            })
            .map_err(|e| e.to_string())?;
    }
    if opened.is_empty() {
        return Err("aucune interface n'a pu être ouverte (droits de capture manquants ?)".into());
    }
    Ok(opened)
}

/// Où commence l'IPv4 selon le type de lien libpcap.
fn link_header_len(link_type: c_int, frame: &[u8]) -> Option<usize> {
    let be16 = |at: usize| frame.get(at..at + 2).map(|b| u16::from_be_bytes([b[0], b[1]]));
    match link_type {
        1 => {
            let mut at = 12;
            for _ in 0..2 {
                match be16(at)? {
                    0x8100 | 0x88A8 => at += 4,
                    _ => break,
                }
            }
            (be16(at)? == 0x0800).then_some(at + 2)
        }
        0 => (frame.get(..4)? == [2, 0, 0, 0]).then_some(4),
        12 | 14 | 101 | 228 => Some(0),
        113 => (be16(14)? == 0x0800).then_some(16),
        276 => (be16(0)? == 0x0800).then_some(20),
        _ => None,
    }
}

fn guess_link_header_len(frame: &[u8]) -> Option<usize> {
    if frame.len() >= 14 && u16::from_be_bytes([frame[12], frame[13]]) == 0x0800 {
        Some(14)
    } else if frame.len() >= 4 && frame[..4] == [2, 0, 0, 0] {
        Some(4)
    } else if !frame.is_empty() && frame[0] >> 4 == 4 {
        Some(0)
    } else {
        None
    }
}

pub fn parse_tcp_payload(frame: &[u8], link_type: c_int, device: &str) -> Option<CapturedPayload> {
    if frame.len() < 4 {
        return None;
    }
    let ip_offset = link_header_len(link_type, frame)
        .filter(|&at| frame.get(at).is_some_and(|b| b >> 4 == 4))
        .or_else(|| guess_link_header_len(frame))?;
    if frame.len() < ip_offset + 20 {
        return None;
    }
    let ip = &frame[ip_offset..];
    if ip[0] >> 4 != 4 || ip[9] != 6 {
        return None;
    }
    let ip_header_len = ((ip[0] & 0x0F) as usize) * 4;
    let tcp_offset = ip_offset + ip_header_len;
    if frame.len() < tcp_offset + 20 {
        return None;
    }
    let tcp = &frame[tcp_offset..];
    let src_port = u16::from_be_bytes([tcp[0], tcp[1]]);
    let dst_port = u16::from_be_bytes([tcp[2], tcp[3]]);
    let tcp_seq = u32::from_be_bytes([tcp[4], tcp[5], tcp[6], tcp[7]]);
    let tcp_ack = u32::from_be_bytes([tcp[8], tcp[9], tcp[10], tcp[11]]);
    let tcp_header_len = ((tcp[12] >> 4) as usize) * 4;
    // Fin réelle = longueur IP (le bourrage Ethernet n'est pas du contenu).
    let ip_total = u16::from_be_bytes([ip[2], ip[3]]) as usize;
    let ip_end = ip_offset + ip_total;
    let end = if ip_total >= ip_header_len + 20 && ip_end <= frame.len() { ip_end } else { frame.len() };
    let start = tcp_offset + tcp_header_len;
    if start >= end {
        return None;
    }
    Some(CapturedPayload {
        src_port,
        dst_port,
        data: frame[start..end].to_vec(),
        device_name: Some(device.to_string()),
        captured_at_ms: now_ms(),
        src_ip: Some(format!("{}.{}.{}.{}", ip[12], ip[13], ip[14], ip[15])),
        dst_ip: Some(format!("{}.{}.{}.{}", ip[16], ip[17], ip[18], ip[19])),
        tcp_seq,
        tcp_ack,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ipv4_tcp() -> Vec<u8> {
        let mut ip = vec![0x45, 0, 0, 42, 0, 0, 0, 0, 64, 6, 0, 0, 10, 0, 0, 2, 193, 202, 112, 99];
        ip.extend_from_slice(&[0xC7, 0x38, 0x34, 0x10, 0, 0, 0, 1, 0, 0, 0, 0, 0x50, 0x18, 0, 0, 0, 0, 0, 0]);
        ip.extend_from_slice(b"hi");
        ip
    }

    #[test]
    fn reads_ethernet_vlan_loopback_raw_and_cooked() {
        for (link, header) in [
            (1, [[0u8; 12].as_slice(), &[0x08, 0x00]].concat()),
            (1, [[0u8; 12].as_slice(), &[0x81, 0x00, 0x00, 0x05, 0x08, 0x00]].concat()),
            (0, vec![2, 0, 0, 0]),
            (12, vec![]),
            (113, [[0u8; 14].as_slice(), &[0x08, 0x00]].concat()),
            (276, [[0x08u8, 0x00].as_slice(), &[0u8; 18]].concat()),
        ] {
            let frame = [header.as_slice(), &ipv4_tcp()].concat();
            let p = parse_tcp_payload(&frame, link, "dev").expect("lu");
            assert_eq!((p.src_port, p.dst_port, p.data.as_slice()), (51000, 13328, b"hi".as_slice()));
        }
    }

    #[test]
    fn ethernet_padding_is_not_payload() {
        let ether = [[0u8; 12].as_slice(), &[0x08, 0x00]].concat();
        let mut ack = ipv4_tcp();
        ack.truncate(40);
        ack[3] = 40;
        let frame = [ether.as_slice(), &ack, &[0xbe, 0xde, 0, 3, 0x66, 0xfd]].concat();
        assert!(parse_tcp_payload(&frame, 1, "dev").is_none());
    }

    #[test]
    fn skips_pseudo_devices() {
        assert!(skip_device("any") && skip_device("docker0") && skip_device("veth12"));
        assert!(!skip_device("enp5s0") && !skip_device("wlan0"));
    }
}
