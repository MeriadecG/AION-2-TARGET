//! Morts de monstres EN COMBAT, attribuées ou non au joueur local (familiers).
//!
//! Preuves utilisées (cœur A2Tools, commit 7186846) :
//!   - `DataStorage::mark_entity_dead` n'est appelé que pour le drapeau de mort 3 (« killed in
//!     combat », `stream_processor.rs`) : une disparition (drapeau 7) ou un chargement de zone
//!     (drapeau 1) n'y entrent PAS ;
//!   - `mob_code(id)` : code de PNJ donné à l'apparition du monstre (espèce) ;
//!   - `get_target_snapshot_light(id)` : dégâts reçus par CE monstre, par acteur ;
//!   - `local_player_id()` + `get_summon_data()` : toi et tes invocations.
//! Verdicts : « mine » (mort en combat après tes dégâts), « other » (frappé seulement par
//! d'autres, aucun dégât observé, mannequin), « uncertain » (personnage local pas encore
//! identifié, espèce inconnue). Chaque mort n'est signalée qu'une fois ; un identifiant oublié
//! par le cœur (changement de zone) peut resservir pour un nouveau monstre.

use std::collections::HashSet;

use a2tools::combat::data_storage::DataStorage;
use a2tools::entity::summon_resolver;
use a2tools::i18n::lookup::NpcLookup;
use serde_json::{json, Value};

use crate::PROTOCOL;

#[derive(Default)]
pub struct KillTracker {
    reported: HashSet<i32>,
}

impl KillTracker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn scan(&mut self, storage: &DataStorage, npcs: &NpcLookup, now_ms: i64) -> Vec<Value> {
        let dead = storage.get_dead_entities();
        // Identifiants oubliés par le cœur (réinitialisation, changement de zone) : réutilisables.
        self.reported.retain(|id| dead.contains(id));
        let mut ids: Vec<i32> = dead.into_iter().filter(|id| !self.reported.contains(id)).collect();
        ids.sort_unstable();
        let mut out = Vec::new();
        let local = storage.local_player_id();
        let summons = storage.get_summon_data();
        for id in ids {
            self.reported.insert(id);
            if storage.is_known_player(id) {
                continue; // un joueur mort n'est pas un monstre
            }
            let code = storage.mob_code(id);
            let snap = storage.get_target_snapshot_light(id);
            let (mut mine, mut total) = (0i64, 0i64);
            if let Some(s) = &snap {
                for (&actor, a) in &s.actors {
                    total += a.total_damage;
                    let owner = summon_resolver::resolve(actor, &summons);
                    if local.is_some_and(|me| me == actor as i64 || me == owner as i64) {
                        mine += a.total_damage;
                    }
                }
            }
            let (verdict, reason) = match (code, local) {
                (Some(c), _) if npcs.is_training_dummy(c) => ("other", "mannequin d'entraînement".to_string()),
                (_, _) if total <= 0 => ("other", "aucun dégât observé sur ce monstre".to_string()),
                (_, None) => ("uncertain", "ton personnage n'est pas encore identifié par le moteur".to_string()),
                (None, _) if mine > 0 => ("uncertain", "espèce inconnue (apparition non vue)".to_string()),
                (_, Some(_)) if mine > 0 => (
                    "mine",
                    format!("mort en combat après tes dégâts ({} % du total)", mine * 100 / total.max(1)),
                ),
                _ => ("other", "frappé seulement par d'autres joueurs".to_string()),
            };
            out.push(json!({
                "v": PROTOCOL, "type": "kill", "entity": id, "code": code,
                "name": code.map(|c| npcs.get_npc_name(c)), "boss": code.is_some_and(|c| npcs.is_boss(c)),
                "verdict": verdict, "reason": reason, "my_damage": mine, "total": total,
                "first_hit": snap.as_ref().map(|s| s.first_damage_time), "t": now_ms,
            }));
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use a2tools::entity::damage_packet::ParsedDamagePacket;

    fn hit(storage: &DataStorage, actor: i32, target: i32, dmg: i32, ts: i64) {
        let mut p = ParsedDamagePacket::new();
        p.set_actor_id(actor);
        p.set_target_id(target);
        p.set_damage(dmg);
        p.set_skill_code(11020000);
        p.set_timestamp(ts);
        storage.append_damage(p);
    }

    fn npcs() -> NpcLookup {
        let n = NpcLookup::new();
        n.load_from_json(r#"{"2100001": {"name": "Papis", "isBoss": false, "isDummy": false},
                             "2000000": {"name": "Mannequin", "isBoss": false, "isDummy": true}}"#);
        n
    }

    #[test]
    fn only_combat_deaths_i_damaged_count_once() {
        let s = DataStorage::new();
        let n = npcs();
        s.set_local_player_id(Some(100));
        for (mob, code) in [(5001, 2100001), (5002, 2100001), (5003, 2100001), (5004, 2000000)] {
            s.append_mob(mob, code);
        }
        hit(&s, 100, 5001, 500, 1); // moi
        hit(&s, 200, 5002, 500, 1); // un autre joueur seulement
        hit(&s, 100, 5004, 50, 1); // mannequin
        // 5003 : jamais frappé ; 5005 : disparu sans mort en combat (rien à signaler)
        let mut k = KillTracker::new();
        assert!(k.scan(&s, &n, 1).is_empty(), "aucune mort : aucun événement");
        for id in [5001, 5002, 5003, 5004] {
            s.mark_entity_dead(id);
        }
        let ev = k.scan(&s, &n, 2);
        let v: Vec<(i64, &str)> = ev.iter().map(|e| (e["entity"].as_i64().unwrap(), e["verdict"].as_str().unwrap())).collect();
        assert_eq!(v, vec![(5001, "mine"), (5002, "other"), (5003, "other"), (5004, "other")]);
        assert_eq!(ev[0]["name"], "Papis");
        assert!(k.scan(&s, &n, 3).is_empty(), "une mort n'est signalée qu'une fois");
    }

    #[test]
    fn unknown_local_player_is_uncertain() {
        let s = DataStorage::new();
        let n = npcs();
        s.append_mob(6001, 2100001);
        hit(&s, 100, 6001, 500, 1);
        s.mark_entity_dead(6001);
        let ev = KillTracker::new().scan(&s, &n, 1);
        assert_eq!(ev[0]["verdict"], "uncertain");
    }
}
