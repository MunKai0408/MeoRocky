//! Phát hiện ngôn ngữ theo từng chuỗi — port của `pb3/sdk/langdetect.py`.
//!
//! Rule-based: script Unicode chiếm đa số -> dấu Latin đặc trưng ->
//! từ chức năng -> mặc định "en" yếu. Logic giữ nguyên bản Python,
//! kể cả thứ tự duyệt dict (quyết định hòa phiếu) và heuristic mặc
//! định. Bảng từ vựng ở `lang_tables.rs` (sinh tự động từ Python).

use crate::lang_tables::{DISTINCTIVE, FUNCTION_WORDS, SIMP_ONLY, TRAD_ONLY};

/// Thứ tự script trong dict `counts` của Python (quyết định hòa phiếu).
/// Chỉ các script không phải Latin tham gia vòng lặp đa số (Latin và
/// other_alpha chỉ cộng vào `total`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Script {
    Han,
    Hiragana,
    Katakana,
    Hangul,
    Cyrillic,
    Greek,
    Arabic,
    Hebrew,
    Thai,
}

const NON_LATIN_ORDER: [Script; 9] = [
    Script::Han,
    Script::Hiragana,
    Script::Katakana,
    Script::Hangul,
    Script::Cyrillic,
    Script::Greek,
    Script::Arabic,
    Script::Hebrew,
    Script::Thai,
];

#[derive(Default, Clone, Copy)]
struct ScriptCounts {
    han: usize,
    hiragana: usize,
    katakana: usize,
    hangul: usize,
    cyrillic: usize,
    greek: usize,
    arabic: usize,
    hebrew: usize,
    thai: usize,
    latin: usize,
    other_alpha: usize,
}

impl ScriptCounts {
    fn get(&self, s: Script) -> usize {
        match s {
            Script::Han => self.han,
            Script::Hiragana => self.hiragana,
            Script::Katakana => self.katakana,
            Script::Hangul => self.hangul,
            Script::Cyrillic => self.cyrillic,
            Script::Greek => self.greek,
            Script::Arabic => self.arabic,
            Script::Hebrew => self.hebrew,
            Script::Thai => self.thai,
        }
    }

    fn total(&self) -> usize {
        self.han
            + self.hiragana
            + self.katakana
            + self.hangul
            + self.cyrillic
            + self.greek
            + self.arabic
            + self.hebrew
            + self.thai
            + self.latin
            + self.other_alpha
    }
}

/// Đếm chữ cái theo script Unicode (chỉ đếm chữ cái, bỏ số/ký hiệu).
fn script_counts(text: &str) -> ScriptCounts {
    let mut c = ScriptCounts::default();
    for ch in text.chars() {
        if !ch.is_alphabetic() {
            continue;
        }
        let o = ch as u32;
        if (0x4E00..=0x9FFF).contains(&o)
            || (0x3400..=0x4DBF).contains(&o)
            || (0xF900..=0xFAFF).contains(&o)
        {
            c.han += 1;
        } else if (0x3040..=0x309F).contains(&o) {
            c.hiragana += 1;
        } else if (0x30A0..=0x30FF).contains(&o) {
            c.katakana += 1;
        } else if (0xAC00..=0xD7A3).contains(&o) || (0x1100..=0x11FF).contains(&o) {
            c.hangul += 1;
        } else if (0x0400..=0x04FF).contains(&o) {
            c.cyrillic += 1;
        } else if (0x0370..=0x03FF).contains(&o) {
            c.greek += 1;
        } else if (0x0600..=0x06FF).contains(&o) {
            c.arabic += 1;
        } else if (0x0590..=0x05FF).contains(&o) {
            c.hebrew += 1;
        } else if (0x0E00..=0x0E7F).contains(&o) {
            c.thai += 1;
        } else if o < 0x0250 || (0x1E00..=0x1EFF).contains(&o) {
            // Latin mo rong (0x0100-0x017F) da nam trong o < 0x0250 —
            // nhanh elif thu 2 cua Python la thua, chi 0x1E00-0x1EFF
            // moi can them; gop chung 1 nhanh (cung cong vao latin).
            c.latin += 1;
        } else {
            c.other_alpha += 1;
        }
    }
    c
}

/// 'Hant' | 'Hans' | None — chỉ để thống kê, không tách file.
pub fn detect_chinese_variant(text: &str) -> Option<&'static str> {
    let tr = text.chars().filter(|ch| TRAD_ONLY.contains(*ch)).count();
    let si = text.chars().filter(|ch| SIMP_ONLY.contains(*ch)).count();
    if tr > 0 && si == 0 {
        return Some("Hant");
    }
    if si > 0 && tr == 0 {
        return Some("Hans");
    }
    if tr > 0 && si > 0 {
        return Some(if tr > si { "Hant" } else { "Hans" });
    }
    None
}

/// Bỏ placeholder/thẻ Unity ({VAR1}, %s, {0}) trước khi phát hiện.
/// Regex Python: `\{[^}]*\}|%[sd]|__PB3PH\d+__` — quét tay, thay bằng " ".
fn clean_for_detect(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0usize;
    while i < chars.len() {
        let ch = chars[i];
        if ch == '{' {
            // Tìm '}' đóng đầu tiên.
            let mut j = i + 1;
            let mut found = false;
            while j < chars.len() {
                if chars[j] == '}' {
                    found = true;
                    break;
                }
                j += 1;
            }
            if found {
                out.push(' ');
                i = j + 1;
                continue;
            }
            out.push(ch);
            i += 1;
            continue;
        }
        if ch == '%' && i + 1 < chars.len() && (chars[i + 1] == 's' || chars[i + 1] == 'd') {
            out.push(' ');
            i += 2;
            continue;
        }
        if ch == '_'
            && text_matches(&chars, i, "__PB3PH")
            && i + 7 < chars.len()
            && chars[i + 7].is_ascii_digit()
        {
            // __PB3PH + 1 chữ số trở lên + __
            let mut j = i + 7;
            while j < chars.len() && chars[j].is_ascii_digit() {
                j += 1;
            }
            if j + 1 < chars.len() && chars[j] == '_' && chars[j + 1] == '_' {
                out.push(' ');
                i = j + 2;
                continue;
            }
        }
        out.push(ch);
        i += 1;
    }
    out
}

fn text_matches(chars: &[char], start: usize, pat: &str) -> bool {
    let p: Vec<char> = pat.chars().collect();
    if start + p.len() > chars.len() {
        return false;
    }
    chars[start..start + p.len()] == p[..]
}

/// Từ trong chuỗi đã làm sạch: regex `[a-zA-Z\u00c0-\u024f\u1e00-\u1eff]+`.
fn extract_words(lowered: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut cur = String::new();
    for ch in lowered.chars() {
        let o = ch as u32;
        let is_word = ch.is_ascii_alphabetic()
            || (0x00C0..=0x024F).contains(&o)
            || (0x1E00..=0x1EFF).contains(&o);
        if is_word {
            cur.push(ch);
        } else if !cur.is_empty() {
            words.push(std::mem::take(&mut cur));
        }
    }
    if !cur.is_empty() {
        words.push(cur);
    }
    words
}

/// (mã ngôn ngữ, độ tin cậy 'strong'|'weak'|'none').
/// Trả (None, "none") khi không có chữ cái nào.
pub fn detect_language(text: &str) -> (Option<&'static str>, &'static str) {
    if text.is_empty() {
        return (None, "none");
    }
    let clean = clean_for_detect(text);
    let counts = script_counts(&clean);
    let total = counts.total();
    if total == 0 {
        return (None, "none");
    }
    // Kana dù ít vẫn là tiếng Nhật — kiểm tra TRƯỚC luật đa số Hán.
    if counts.hiragana > 0 || counts.katakana > 0 {
        return (Some("ja"), "strong");
    }
    // Chuỗi lẫn script: script không phải Latin có >= 2 ký tự và >= 20%.
    let mut non_latin: Vec<(Script, usize)> = NON_LATIN_ORDER
        .iter()
        .map(|s| (*s, counts.get(*s)))
        .collect();
    // Sắp xếp ổn định theo số lượng giảm dần (hòa giữ thứ tự dict).
    non_latin.sort_by_key(|a| std::cmp::Reverse(a.1));
    for (script, n) in &non_latin {
        if *n >= 2 && *n * 5 >= total {
            if *script == Script::Cyrillic {
                if clean.chars().any(|ch| "іїєґІЇЄҐ".contains(ch)) {
                    return (Some("uk"), "strong");
                }
                return (Some("ru"), "strong");
            }
            let code = match script {
                Script::Han => Some("zh"),
                Script::Hangul => Some("ko"),
                Script::Greek => Some("el"),
                Script::Arabic => Some("ar"),
                Script::Hebrew => Some("he"),
                Script::Thai => Some("th"),
                _ => None,
            };
            if let Some(code) = code {
                return (Some(code), "strong");
            }
        }
    }
    // Script không phải Latin chiếm đa số tuyệt đối -> mạnh.
    // max() của Python trả max ĐẦU TIÊN theo thứ tự dict khi hòa.
    let mut best_script = NON_LATIN_ORDER[0];
    let mut best_n = counts.get(best_script);
    for s in NON_LATIN_ORDER.iter().skip(1) {
        let n = counts.get(*s);
        if n > best_n {
            best_n = n;
            best_script = *s;
        }
    }
    if best_n > 0 && best_n * 2 >= total {
        if best_script == Script::Cyrillic {
            if clean.chars().any(|ch| "іїєґІЇЄҐ".contains(ch)) {
                return (Some("uk"), "strong");
            }
            return (Some("ru"), "strong");
        }
        let code = match best_script {
            Script::Han => Some("zh"),
            Script::Hangul => Some("ko"),
            Script::Greek => Some("el"),
            Script::Arabic => Some("ar"),
            Script::Hebrew => Some("he"),
            Script::Thai => Some("th"),
            _ => None,
        };
        if let Some(code) = code {
            return (Some(code), "strong");
        }
    }
    // Latin: dấu đặc trưng.
    let mut mark_scores: Vec<(&'static str, usize)> = Vec::new();
    for (lang, chars) in DISTINCTIVE {
        let n = clean.chars().filter(|ch| chars.contains(*ch)).count();
        if n > 0 {
            mark_scores.push((lang, n));
        }
    }
    if !mark_scores.is_empty() {
        mark_scores.sort_by_key(|a| std::cmp::Reverse(a.1));
        if mark_scores.len() == 1 || mark_scores[0].1 > mark_scores[1].1 {
            return (Some(mark_scores[0].0), "strong");
        }
        // Hòa dấu -> để từ chức năng quyết.
    }
    // Latin: từ chức năng.
    let lowered = clean.to_lowercase();
    let words = extract_words(&lowered);
    if !words.is_empty() {
        let mut wordset: Vec<&str> = words.iter().map(|w| w.as_str()).collect();
        wordset.sort_unstable();
        wordset.dedup();
        let mut scores: Vec<(&'static str, usize)> = Vec::new();
        for (lang, vocab) in FUNCTION_WORDS {
            let hits = wordset
                .iter()
                .filter(|w| vocab.binary_search(w).is_ok())
                .count();
            if hits > 0 {
                scores.push((lang, hits));
            }
        }
        if !scores.is_empty() {
            // (-hits, lang) — hòa điểm thì mã theo thứ tự chữ cái.
            scores.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(b.0)));
            if scores.len() == 1 || scores[0].1 > scores[1].1 {
                return (Some(scores[0].0), "strong");
            }
            if scores[0].1 >= 2 {
                return (Some(scores[0].0), "weak");
            }
            // 1 từ, hòa nhiều ngôn ngữ -> en mặc định (rơi xuống dưới).
        }
    }
    // Mặc định Latin trơn = tiếng Anh (yếu).
    if counts.latin > 0 {
        return (Some("en"), "weak");
    }
    (None, "none")
}

/// Mã ngôn ngữ của chuỗi, hoặc None khi không có chữ cái nào.
pub fn classify(text: &str) -> Option<&'static str> {
    detect_language(text).0
}

/// Học ngôn ngữ theo VỊ TRÍ trong các nhóm lặp (vd 1 term + N bản dịch).
/// Chỉ dùng phiếu 'strong' để học.
pub fn infer_positions(
    groups: &[Vec<String>],
    min_votes: usize,
    min_share: f64,
) -> Vec<Option<&'static str>> {
    if groups.is_empty() {
        return Vec::new();
    }
    let width = groups[0].len();
    let mut out: Vec<Option<&'static str>> = Vec::with_capacity(width);
    for j in 0..width {
        let mut votes: Vec<(&'static str, usize)> = Vec::new();
        for g in groups {
            if j >= g.len() {
                continue;
            }
            let (code, conf) = detect_language(&g[j]);
            if conf == "strong" {
                if let Some(code) = code {
                    match votes.iter_mut().find(|(c, _)| *c == code) {
                        Some((_, n)) => *n += 1,
                        None => votes.push((code, 1)),
                    }
                }
            }
        }
        let total: usize = votes.iter().map(|(_, n)| *n).sum();
        if total >= min_votes && !votes.is_empty() {
            // max() trả max đầu tiên (thứ tự xuất hiện đầu tiên khi hòa).
            let mut top = votes[0].0;
            let mut n_top = votes[0].1;
            for (c, n) in votes.iter().skip(1) {
                if *n > n_top {
                    n_top = *n;
                    top = *c;
                }
            }
            if n_top as f64 / total as f64 >= min_share {
                out.push(Some(top));
                continue;
            }
        }
        out.push(None);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kana_wins_over_han() {
        assert_eq!(detect_language("すばらしいわ！"), (Some("ja"), "strong"));
    }

    #[test]
    fn hangul() {
        assert_eq!(detect_language("엄청나구만！").0, Some("ko"));
    }

    #[test]
    fn cyrillic() {
        assert_eq!(detect_language("Необыкновенно!").0, Some("ru"));
    }

    #[test]
    fn han() {
        assert_eq!(detect_language("丹青妙手，神来之笔啊！").0, Some("zh"));
    }

    #[test]
    fn no_letters() {
        assert_eq!(detect_language("123 !!!"), (None, "none"));
    }

    #[test]
    fn german_sentence() {
        assert_eq!(
            detect_language("Häufe in einem einzigen Durchlauf 999+ Gold an."),
            (Some("de"), "strong")
        );
    }

    #[test]
    fn dutch_sentence() {
        assert_eq!(
            detect_language("Bezit 999 of meer goudstukken in één run."),
            (Some("nl"), "strong")
        );
    }

    #[test]
    fn french_sentence() {
        assert_eq!(
            detect_language("Accumulez 999+ d'or en une partie."),
            (Some("fr"), "strong")
        );
    }

    #[test]
    fn plain_latin_defaults_to_en_weak() {
        assert_eq!(
            detect_language("Save 999+ gold in a single run."),
            (Some("en"), "weak")
        );
    }

    #[test]
    fn danish() {
        assert_eq!(detect_language("Vind ét spil.").0, Some("da"));
    }

    #[test]
    fn romanian() {
        assert_eq!(
            detect_language("Passpartout face doar capodopere!").0,
            Some("ro")
        );
    }

    #[test]
    fn portuguese() {
        assert_eq!(
            detect_language("Passpartout só faz obras-primas!").0,
            Some("pt")
        );
    }

    #[test]
    fn czech_and_slovak() {
        assert_eq!(
            detect_language("Veľmi pekne ďakujem za pomoc.").0,
            Some("sk")
        );
        assert_eq!(
            detect_language("Děkuji mockrát za pomoc.").0,
            Some("cs")
        );
    }

    #[test]
    fn hungarian_and_tagalog() {
        assert_eq!(detect_language("Nagyon szépen köszönöm.").0, Some("hu"));
        assert_eq!(
            detect_language("Maraming salamat sa tulong.").0,
            Some("tl")
        );
    }

    #[test]
    fn homograph_tie_breakers() {
        assert_eq!(detect_language("A most liberal choice of colour.").0, Some("en"));
        assert_eq!(
            detect_language("I would've chosen such fantastic colours.").0,
            Some("en")
        );
        assert_eq!(detect_language("dove-grey").0, Some("en"));
    }

    #[test]
    fn french_circumflex_i_not_romanian() {
        assert_eq!(
            detect_language("Originalité s'il vous plaît!").0,
            Some("fr")
        );
    }

    #[test]
    fn infer_positions_from_strong_votes() {
        let groups: Vec<Vec<String>> = vec![
            vec![
                "Win your first game.".into(),
                "Ich habe das Spiel gewonnen.".into(),
                "Vous avez gagné la partie.".into(),
                "赢得第一场比赛。".into(),
            ],
            vec![
                "You have won ten games.".into(),
                "Ich habe zehn Spiele gewonnen.".into(),
                "Vous avez gagné dix parties.".into(),
                "赢得十场比赛。".into(),
            ],
            vec![
                "You have won a hundred games.".into(),
                "Ich habe hundert Spiele gewonnen.".into(),
                "Vous avez gagné cent parties.".into(),
                "赢得一百场比赛。".into(),
            ],
            vec![
                "You have won a thousand games.".into(),
                "Ich habe tausend Spiele gewonnen.".into(),
                "Vous avez gagné mille parties.".into(),
                "赢得一千场比赛。".into(),
            ],
            vec![
                "You have won all the games.".into(),
                "Ich habe jedes Spiel gewonnen.".into(),
                "Vous avez gagné chaque partie.".into(),
                "赢得每场比赛。".into(),
            ],
            vec![
                "You have won the last game.".into(),
                "Ich habe das letzte Spiel gewonnen.".into(),
                "Vous avez gagné la dernière partie.".into(),
                "赢得最后一场比赛。".into(),
            ],
        ];
        assert_eq!(
            infer_positions(&groups, 5, 0.60),
            vec![Some("en"), Some("de"), Some("fr"), Some("zh")]
        );
    }

    #[test]
    fn chinese_variant() {
        assert_eq!(detect_chinese_variant("學國會"), Some("Hant"));
        assert_eq!(detect_chinese_variant("学国会"), Some("Hans"));
        assert_eq!(detect_chinese_variant("Hello"), None);
    }

    #[test]
    fn clean_placeholders() {
        // "Hello" + 6 khoang (1 goc + {NAME} + 1 goc + %s + 1 goc + __PB3PH12__) + "!".
        assert_eq!(clean_for_detect("Hello {NAME} %s __PB3PH12__!"), "Hello      !");
        assert_eq!(clean_for_detect("a {unclosed"), "a {unclosed");
        assert_eq!(clean_for_detect("100%s done"), "100  done");
    }
}
