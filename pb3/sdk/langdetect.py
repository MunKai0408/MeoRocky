"""Phát hiện ngôn ngữ theo từng chuỗi (rule-based, chỉ thư viện chuẩn).

Dùng cho: (A) tách file MonoBehaviour trộn nhiều ngôn ngữ thành file sạch
mỗi ngôn ngữ + (A) lọc hàng đợi dịch theo ngôn ngữ nguồn đã chọn.

Cách làm — KHÔNG port nguyên si từ bất kỳ tool nào khác, mà theo kiến trúc
web BearTea: module Python chạy trong worker Pyodide (cùng tiến trình với
pipeline trích xuất), không mạng, không model ngoài, RAM O(len(text)):
  1) Script Unicode chiếm đa số (Han/Kana/Hangul/Cyrillic/Hy Lạp/Arabic/
     Hebrew/Thái) -> ngôn ngữ của script đó (mạnh, gần như không sai).
  2) Chữ Latin có dấu đặc trưng riêng của một ngôn ngữ (ñ, ã, ß, ł, ă...)
     -> ngôn ngữ đó (mạnh).
  3) Từ chức năng (function words: the/and/der/die/el/la/le/les...) — mỗi
     ngôn ngữ một danh sách nhỏ (~30 từ) — -> ngôn ngữ có nhiều từ khớp
     nhất (trung bình; chuỗi game thường ngắn nên chỉ cần 1-2 từ).
  4) Còn lại (Latin trơn, không dấu, không từ chức năng nhận ra): mặc định
     "en" (yếu). Lý do: ngôn ngữ gốc mặc định của game Unity hầu như luôn
     là tiếng Anh, và 2 luồng chính của Joseph là en/zh. Đây là heuristic
     CÓ GHI RÕ độ tin cậy "weak" — không giả vờ chắc chắn.
  5) Chuỗi không có chữ cái nào -> (None, "none").

Giới hạn đã biết (ghi thật, không giấu):
  - Chuỗi Hán thuần ngắn không kana (vd "日本語") phân loại zh (đáng lẽ ja).
  - Tiếng Ukraina/Bulgaria gộp vào ru khi không có chữ đặc trưng (і/ї/є
    thì nhận ra uk).
  - Từ mượn/lẫn ngôn ngữ trong một chuỗi (vd "Achievement_HisWinningness")
    là định danh, không phải câu — vẫn xếp en theo luật 4.
  - Hans/Hant đều trả "zh" (pipeline dùng chung mã zh); có hàm riêng
    detect_chinese_variant() chỉ để thống kê.

Độ chính xác đo được trên game thật (xem tests/test_langdetect.py):
  Passpartout (nhóm 15 ngôn ngữ, ground truth = thứ tự lần xuất hiện) và
  9 Kings (bảng term: 1 term + 29 bản dịch theo thứ tự cố định).
"""
from __future__ import annotations

import json
import re
from typing import Dict, Optional, Tuple

# Cầu nối Rust/WASM (chỉ có trên web khi worker.js đã nạp wasm):
# pb3_rust_detect_language(text) -> JSON '["zh","strong"]' | '[null,"none"]';
# pb3_rust_detect_chinese_variant(text) -> "Hant"/"Hans"/null.
# Vắng cầu nối (CPython/iSH) hoặc cầu nối hỏng -> rơi về logic Python
# như trước, không đổi hành vi. _RS_BROKEN tắt cầu nối cho cả module
# sau 1 lỗi lạ (giống _lz4_js_broken của UnityBackend).
_RS_BROKEN = False
_MISSING = object()


def _rust_fn(name):
    """Hàm cầu nối Rust theo tên, hoặc _MISSING khi vắng/hỏng."""
    global _RS_BROKEN
    if _RS_BROKEN:
        return _MISSING
    try:
        import js as _js
        fn = getattr(_js, name, None)
    except Exception:
        fn = None
    return fn if fn is not None else _MISSING


def _detect_language_rs(text):
    """(code, conf) qua Rust/WASM, hoặc None khi vắng cầu nối / cầu nối hỏng."""
    global _RS_BROKEN
    fn = _rust_fn("pb3_rust_detect_language")
    if fn is _MISSING:
        return None
    try:
        out = fn(text)
    except Exception:  # noqa: BLE001 - lỗi cầu nối -> tắt, rơi về Python
        _RS_BROKEN = True
        return None
    try:
        code, conf = json.loads(str(out))
    except Exception:  # noqa: BLE001 - trả về lạ -> tắt, rơi về Python
        _RS_BROKEN = True
        return None
    if conf not in ("strong", "weak", "none"):
        _RS_BROKEN = True
        return None
    return code, conf


def _detect_chinese_variant_rs(text):
    """'Hant'/'Hans'/None qua Rust/WASM, hoặc _MISSING khi vắng/hỏng."""
    global _RS_BROKEN
    fn = _rust_fn("pb3_rust_detect_chinese_variant")
    if fn is _MISSING:
        return _MISSING
    try:
        out = fn(text)
    except Exception:  # noqa: BLE001
        _RS_BROKEN = True
        return _MISSING
    if out is None:
        return None
    if isinstance(out, str) and out in ("Hant", "Hans"):
        return out
    _RS_BROKEN = True
    return _MISSING

# ------------------------------------------------------------------ script
def _script_counts(text: str) -> Dict[str, int]:
    """Đếm chữ cái theo script Unicode (chỉ đếm chữ cái, bỏ số/ký hiệu)."""
    counts = {"han": 0, "hiragana": 0, "katakana": 0, "hangul": 0,
              "cyrillic": 0, "greek": 0, "arabic": 0, "hebrew": 0,
              "thai": 0, "latin": 0, "other_alpha": 0}
    for ch in text:
        if not ch.isalpha():
            continue
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF:
            counts["han"] += 1
        elif 0x3040 <= o <= 0x309F:
            counts["hiragana"] += 1
        elif 0x30A0 <= o <= 0x30FF:
            counts["katakana"] += 1
        elif 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF:
            counts["hangul"] += 1
        elif 0x0400 <= o <= 0x04FF:
            counts["cyrillic"] += 1
        elif 0x0370 <= o <= 0x03FF:
            counts["greek"] += 1
        elif 0x0600 <= o <= 0x06FF:
            counts["arabic"] += 1
        elif 0x0590 <= o <= 0x05FF:
            counts["hebrew"] += 1
        elif 0x0E00 <= o <= 0x0E7F:
            counts["thai"] += 1
        elif o < 0x0250:
            counts["latin"] += 1
        elif 0x1E00 <= o <= 0x1EFF or 0x0100 <= o <= 0x017F:
            # Latin mở rộng (ă, â, đ, ł, ñ...) vẫn là Latin — tính riêng
            # ở bước dấu đặc trưng, nhưng ở đây gộp vào latin.
            counts["latin"] += 1
        else:
            counts["other_alpha"] += 1
    return counts


# ------------------------------------------------------------------ Hán phồn/giản
# Chữ chỉ có ở phồn thể / chỉ có ở giản thể (mẫu nhỏ, đủ cho thống kê).
_TRAD_ONLY = set("學國會個來還這們讓點頭體戰麼廣廠寫讀邊長門問間東車馬魚鳥龍龜歲處聲聽說話語認識萬與業樣歲")
_SIMP_ONLY = set("学国会个来还这们让点头体战么广厂写读边长门问间东车马鱼鸟龙龟岁处声听说话语认识万与业样岁")


def detect_chinese_variant(text: str) -> Optional[str]:
    """'Hant' | 'Hans' | None — chỉ để thống kê, không tách file."""
    v = _detect_chinese_variant_rs(text)
    if v is not _MISSING:
        return v
    tr = sum(1 for ch in text if ch in _TRAD_ONLY)
    si = sum(1 for ch in text if ch in _SIMP_ONLY)
    if tr and not si:
        return "Hant"
    if si and not tr:
        return "Hans"
    if tr and si:
        return "Hant" if tr > si else "Hans"
    return None


# ------------------------------------------------------------------ dấu Latin
# Ký tự gần như chỉ xuất hiện ở một ngôn ngữ (đủ để kết luận mạnh).
_DISTINCTIVE = {
    "es": set("ñ¡¿"),
    "pt": set("ãõ"),
    "de": set("ß"),
    "pl": set("ąćęłńśźż"),
    "cs": set("čďěňřšťůž"),
    "sk": set("ľĺŕ"),
    "ro": set("ăâșț"),  # 'î' bỏ ra: tiếng Pháp cũng dùng î (plaît, naître)
    "hu": set("őű"),
    "tr": set("ğış"),
    "vi": set("ơưđ"),
    "da": set("æø"),
    "sv": set("å"),
    "ca": set("·"),
}
# Dấu dùng chung nhiều ngôn ngữ (chỉ tính khi không có dấu đặc trưng):
# à/è/é/ê (fr/it/es), ä/ö/ü (de/sv/fi), ç (fr/pt/tr), ì/ò/ù (it)...


# ------------------------------------------------------------------ từ chức năng
# Chỉ từ chức năng (mạo từ/giới từ/đại từ), KHÔNG dùng từ nội dung game
# (play/win/game...) vì chúng là từ mượn xuất hiện ở mọi ngôn ngữ.
_FUNCTION_WORDS = {
    "en": set("the and you your to is are for with on this that it all me my we our have has will can not but as at be was were are you he she they his her their from by ten most ve dove".split()),
    # 'most' (hu: "bây giờ"), 've' (tr: "và" — mảnh vỡ của would've/could've),
    # 'dove' (it: "ở đâu" — nhưng cũng là danh từ/tên màu tiếng Anh) đều là
    # từ đồng hình: chỉ để HÒA phiếu (không cho ngôn ngữ kia thắng 1-0), không
    # dùng làm bằng chứng mạnh cho tiếng Anh.
    "de": set("der die das und ist ein eine nicht mit von zu für ich du er sie wir ihr den dem des auch wie was wenn aber oder am im es sind war sein haben kann nur schon noch sehr wieder immer kein keine gut alle wer wo wann weil dass nach vor aus bei ohne durch unter einem einen einer eines diesem dieser dieses jenem jener jenes welchem welcher welches meinem meinen meiner deinem seinem ihrem unserem eurem jedem jeder jedes manchem mancher manches vielen vieler wenige wenigen einige einigen einiger keinem keinen keiner keines muss müssen soll sollen wollen wollte können könnte dürfen darf mögen mag werden wird wurde wurden bin bist seid waren machen machte gemacht gehen geht ging kommen kommt kam sehen sieht sah wissen weiß geben gibt gab nehmen nimmt nahm finden findet fand bleiben bleibt blieb stehen steht stand liegen liegt lag heißen heißt hieß meint sagen sagt fragen fragt spielen spielt arbeiten arbeitet leben lebt lieben liebt will ja habe hast hat habt".split()),
    "es": set("el la los las una uno y de que es por con su sus para un una los del al se lo como más pero sus este esta esto son está también muy porque cuando donde así bien siempre ahora hoy ayer mañana ya todavía aún hasta desde sobre bajo tras ante contra hacia según sin nuestro vuestro ese esa eso aquel aquella hay tiene tienen puede pueden debe deben quiero quieres mira oye gracias bueno pequeño nuevo viejo mucho poco cada otro otra misma mismo".split()),
    "fr": set("le la les des une un et est de à que pour dans ce vous nous il ils ce cette sont aux du des par sur pas plus au son sa ses aussi très mais parce quand où comme ainsi bien tout tous rien toujours jamais maintenant aujourd hier demain déjà encore jusqu depuis sous chez sans mon ma mes ton ta tes notre votre leur leurs cet ces celui celle elle elles suis sommes êtes avons avez ont peux peut peuvent dois doivent veux veulent vais vas va allons allez vont viens vient voir dis dit merci bon grand petit nouveau vieux beaucoup peu chaque autre même non ne".split()),
    "it": set("il lo la gli le di che e è non una uno sono per con del della da un su come più anche ma se anche molto perché quando dove così bene tutto tutti niente nessuno qualcuno sempre mai adesso oggi ieri domani già ancora fino tra sotto senza mio mia miei mie tuo tua tuoi tue suo sua suoi sue nostro vostra questo questa questi queste quello quella chi cui io lui lei noi voi loro sei ho hai ha abbiamo avete hanno posso può possono devo devono voglio vogliono vado vai va andiamo andate vanno vengo viene vedere dico dice grazie buono piccolo nuovo vecchio ogni altro altra stesso stessa".split()),
    "pt": set("o os as um uma e é de em que para com não por se os do da dos mais como ao aos foi sua seu suas seus vossa vosso também só faz muito mas porque quando onde assim bem tudo todos ninguém alguém sempre agora hoje ontem amanhã já ainda até desde sob sem meu minha meus minhas teu tua teus tuas nosso esse essa isso aquele aquela eu ele ela nós vós eles elas sou és somos são tenho tens tem temos têm posso pode podem devo devem quero queres vou vais vai ides vão venho vem ver digo diz obrigado bom pequeno novo velho outro outra mesmo mesma este esta estes estas".split()),
    "nl": set("de het een van ik te dat is niet een op aan met voor zijn hij zij wij jullie dit deze die alleen maar ook nog mijn jouw haar hun wat waar hoe waarom geen veel toen dan dus toch even heel altijd nooit nu vandaag gisteren al steeds weer hier daar wanneer omdat bij door uit na je hebben heeft heb hebt wordt worden kunnen moet moeten zal zullen willen gaat gaan komt komen ziet zien weet weten geeft geven neemt nemen vindt vinden blijft blijven staat staan ligt liggen heet heten zegt zeggen vraagt vragen speelt spelen werkt werken leeft leven houdt houden goed groot nieuw oud jong hoog weinig beide bezit krijg krijgt krijgen maak maakt maken doe doet doen ga zie geef neem blijf sta lig zeg vraag speel leef houd ben bent word laat laten één".split()),
    "pl": set("nie w na z do to się że jest jak po za ale czy już tylko bardzo tylko też bardzo już jeszcze zawsze nigdy teraz dzisiaj wczoraj jutro tutaj tam gdzie kiedy dlaczego ponieważ lub oraz od dla przez bez nad pod przed przy albo tak więc może musi chce mogę możesz wiem wiesz mam masz ma mamy macie mają są był była było byli ten ta ci te tamten mój moja moje twój twoja swój swoja nasz wasz ich jego jej dobry duży mały nowy stary dużo mało każdy inny sam sama".split()),
    "cs": set("jsem také ještě teď zítra tady kdy proč děkuji hodně velmi jako nebo ani už jen každý jiný dobrý velký malý nový starý svůj můj tvůj jeho její náš váš jejich jsi jsme jste jsou byl byla bylo byli mám máš má máme máte mají musím může chci vím jdu jde vidí říká prosím kde kdo který tato tento tyto tamten tohle tamto právě opravdu málo docela skoro brzy pozdě dnes včera kam odkud protože když jestli aby ačkoli přesto proto takže tedy mezi přes skrz podle proti kvůli díky během kolem za po jak ale".split()),
    "sk": set("aj veľmi ešte teraz zajtra kedy prečo ako áno ďakujem veľa som si už len každý iný dobrý veľký malý nový starý svoj môj tvoj jeho jej náš váš ich sme ste sú bol bola bolo boli mám máš má máme máte majú musím môže chcem viem idem ide vidí hovorí prosím kde kto ktorý táto tento tieto tamten toto tamto práve naozaj málo dosť skoro čoskoro neskoro dnes včera kam odkiaľ pretože keď či aby hoci napriek preto takže teda medzi cez podľa proti kvôli vďaka počas okolo za po jak ale".split()),
    "ro": set("și cu pe că este un o din la cel mai sunt pentru care dacă nu numai doar face foarte deja încă mereu niciodată acum astăzi ieri mâine aici acolo unde când dar sau cum cât cine care acesta aceasta acești acela aceea el ea noi voi ei ele ești suntem am ai are avem aveți au pot poate trebuie vreau vrei vrea vrem vor merg mergi merge vino vine vezi vede spune zice mulțumesc bun mare mic nou vechi mult puțin fiecare alt alta același aceeași".split()),
    "sv": set("och att det är ett inte jag du han hon vi de som på av för till med var bara gör endast också mycket redan ännu alltid aldrig nu idag igår imorgon här där när varför eftersom vad vem vilken detta dessa den min mitt mina din ditt dina sin sitt sina vår vårt era ert hade ska skall kunde vill ville måste får fick vet veta säger säga tack bra stor liten ny gammal lite varje annan samma språk".split()),
    "da": set("og at det er et ikke jeg du han hun vi de som på af for til med var kun også meget allerede endnu altid aldrig nu idag igår imorgen her der hvor hvornår hvorfor fordi hvad hvem hvilken dette disse den min mit mine din dit dine sin sit sine vor vort har havde skal kan kunne vil ville måtte får fik ved vide siger sige tak god stor lille ny gammel lidt hver anden samme mig sprog nemlig dog gerne derfor altså hans hendes deres ét".split()),
    "no": set("og at det er et ikke jeg du han hun vi de som på av for til med var bare også mye allerede ennå alltid aldri nå idag igår imorgen her der hvor når hvorfor fordi hva hvem hvilken dette disse min mitt mine din ditt dine sin sitt sine vår vårt skal kunne ville måtte får fikk vet vite sier si takk god stor liten ny gammel lite hver annen samme meg språk derfor altså denne gjerne vil den hans hennes deres".split()),
    "fi": set("ja on ei se että kun niin kuin jos mutta myös vain paljon jo vielä aina koskaan nyt tänään eilen huomenna täällä siellä missä milloin miksi koska mitä kuka mikä tämä nämä tuo minun sinun hänen meidän teidän heidän olen olet olemme olette ovat minulla sinulla voi voin voit täytyy haluan menen tulee sanoo sanoa kiitos hyvä suuri pieni uusi vanha vähän joka toinen sama".split()),
    "hu": set("és hogy nem volt lesz egy mikor hol miért mert már még csak nagyon minden semmi senki valaki most tegnap holnap itt ott hová honnan én ő ők engem téged minket titeket őket ez az ezek azok ilyen olyan jó nagy kicsi új régi sok kevés más ugyanaz".split()),
    "tr": set("ve bir bu ne de da için çok sonra önce gibi sadece daha hiç hep her zaman şimdi bugün dün yarın burada orada nerede neden çünkü ama veya mi benim senin onun bizim sizin onların ben sen o biz siz onlar var yok olmak ediyor diyor teşekkür iyi büyük küçük yeni eski az başka aynı".split()),
    "vi": set("và của là không các đã cho này những được với trong chỉ cũng rất đang sẽ luôn bao giờ khi đâu nào vì nhưng hoặc trên dưới trước sau ngoài tôi bạn anh chị em chúng họ nó kia đó đây mình có bị phải muốn đi đến làm nói xem nghe biết cảm ơn tốt lớn nhỏ mới cũ nhiều ít mỗi khác giống".split()),
    "id": set("dan yang di ke dari untuk tidak dengan pada adalah hanya juga sangat sudah masih selalu pernah sekarang hari ini kemarin besok sini sana mana kapan mengapa karena tapi atau saya kamu dia kami kita mereka itu milik ada bisa harus mau pergi datang lihat dengar tahu terima kasih baik besar kecil baru lama banyak sedikit setiap lain sama".split()),
    # Tiếng Mã Lai (ms) gần như trùng từ vựng với tiếng Indonesia (id):
    # chỉ phân biệt được nhờ vài từ riêng (kerana/ialah/tak thay cho
    # karena/adalah/tidak). Chuỗi chỉ có từ chung sẽ xếp "id" (hòa điểm
    # thì id thắng theo thứ tự chữ cái) — xem _source_match: họ ms/id
    # được chấp nhận lẫn nhau khi lọc ngôn ngữ nguồn.
    "ms": set("dan yang di ke dari untuk tidak dengan pada adalah hanya juga sangat sudah masih selalu pernah sekarang hari ini semalam esok sini sana mana bila mengapa kerana tapi atau saya kamu dia kami kita mereka itu milik ada boleh mesti akan telah sedang tak ialah seperti antara apabila bagaimana siapa apa selamat besar kecil baru lama banyak sedikit setiap lain sama terima kasih jangan sila".split()),
    "tl": set("ang ng mga sa ay at na ko mo siya kami tayo ito iyan iyon hindi oo salamat mabuti malaki maliit bago luma marami kaunti lahat iba pareho ako ikaw sila kayo dito diyan doon saan kailan bakit dahil pero kung kapag habang pagkatapos araw gabi tao bahay tubig pagkain mahal masama gusto ayaw kailangan maaari pwede sige tara halika tingnan pakinggan alam kilala walang mayroon meron wala".split()),
}
_WORD_RE = re.compile(r"[a-zA-Z\u00c0-\u024f\u1e00-\u1eff]+")

# Placeholder/thẻ Unity ({VAR1}, %s, {0}) chứa chữ Latin gây nhiễu -> bỏ
# trước khi phát hiện (chúng không phải ngôn ngữ của chuỗi).
_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}|%[sd]|__PB3PH\d+__")


def _clean_for_detect(text: str) -> str:
    return _PLACEHOLDER_RE.sub(" ", text or "")


def detect_language(text: str) -> Tuple[Optional[str], str]:
    """(mã ngôn ngữ, độ tin cậy 'strong'|'weak'|'none').

    - 'strong': script chiếm đa số / dấu đặc trưng / từ chức năng thắng
      cách biệt (>= 2 từ, hoặc 1 từ + 1 dấu).
    - 'weak': Latin trơn -> mặc định 'en' (heuristic, xem docstring đầu file).
    - 'none': không có chữ cái nào -> (None, 'none').
    """
    if not isinstance(text, str) or not text:
        return None, "none"
    rs = _detect_language_rs(text)
    if rs is not None:
        return rs
    clean = _clean_for_detect(text)
    counts = _script_counts(clean)
    total = sum(counts.values())
    if total == 0:
        return None, "none"
    # Kana dù ít vẫn là tiếng Nhật (tiếng Nhật luôn có kana trừ chuỗi toàn
    # kanji ngắn; tiếng Trung không bao giờ có kana) — kiểm tra TRƯỚC
    # luật đa số Han, nếu không chuỗi Nhật nhiều kanji sẽ bị xếp nhầm zh.
    if counts["hiragana"] or counts["katakana"]:
        return "ja", "strong"
    # Chuỗi lẫn script (vd tên Latin + câu Hán "Passpartout！只為巨作帶鹽！"):
    # script không phải Latin có >= 2 ký tự và >= 20% chữ cái thì vẫn là
    # ngôn ngữ của script đó — luật đa số tuyệt đối bỏ qua các chuỗi này.
    non_latin = {k: v for k, v in counts.items()
                 if k not in ("latin", "other_alpha")}
    for script, n in sorted(non_latin.items(), key=lambda kv: -kv[1]):
        if n >= 2 and n * 5 >= total:
            if script == "cyrillic":
                if any(ch in clean for ch in "іїєґІЇЄҐ"):
                    return "uk", "strong"
                return "ru", "strong"
            code = {"han": "zh", "hangul": "ko", "greek": "el",
                    "arabic": "ar", "hebrew": "he", "thai": "th"}.get(script)
            if code:
                return code, "strong"
    # -- script không phải Latin chiếm đa số tuyệt đối -> mạnh.
    best_script = max(non_latin.items(), key=lambda kv: kv[1])
    if best_script[1] and best_script[1] * 2 >= total:
        code = {"han": "zh",
                "hangul": "ko", "cyrillic": None, "greek": "el",
                "arabic": "ar", "hebrew": "he", "thai": "th"}.get(best_script[0])
        if best_script[0] == "cyrillic":
            # Ukraina: і/ї/є/ґ gần như chỉ có ở tiếng Ukraina.
            if any(ch in clean for ch in "іїєґІЇЄҐ"):
                return "uk", "strong"
            return "ru", "strong"
        if code:
            return code, "strong"
    # -- Latin: dấu đặc trưng.
    mark_scores: Dict[str, int] = {}
    for lang, chars in _DISTINCTIVE.items():
        n = sum(1 for ch in clean if ch in chars)
        if n:
            mark_scores[lang] = n
    if mark_scores:
        # 'å' (sv) và 'æø' (da) hay lẫn nhau; 'ç' không có trong bảng này.
        top = sorted(mark_scores.items(), key=lambda kv: -kv[1])
        if len(top) == 1 or top[0][1] > top[1][1]:
            return top[0][0], "strong"
        # Hòa dấu -> để từ chức năng quyết (vd chuỗi có cả ñ và ç).
    # -- Latin: từ chức năng.
    words = _WORD_RE.findall(clean.lower())
    if words:
        wordset = set(words)
        scores: Dict[str, int] = {}
        for lang, vocab in _FUNCTION_WORDS.items():
            hits = len(wordset & vocab)
            if hits:
                scores[lang] = hits
        if scores:
            top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
            if len(top) == 1 or top[0][1] > top[1][1]:
                return top[0][0], "strong"
            if top[0][1] >= 2:
                return top[0][0], "weak"
            # 1 từ, hòa nhiều ngôn ngữ (vd "in" vừa en vừa nl) -> en mặc định.
    # -- Mặc định Latin trơn = tiếng Anh (yếu, xem docstring đầu file).
    if counts["latin"]:
        return "en", "weak"
    return None, "none"


def classify(text: str) -> Optional[str]:
    """Mã ngôn ngữ của chuỗi, hoặc None khi không có chữ cái nào."""
    code, _conf = detect_language(text)
    return code


def infer_positions(groups, min_votes: int = 5, min_share: float = 0.60):
    """Học ngôn ngữ theo VỊ TRÍ trong các nhóm lặp (vd 1 term + N bản dịch,
    hay 1 key lặp lại ở N object mỗi object một ngôn ngữ).

    groups: danh sách các nhóm, mỗi nhóm là list chuỗi CÙNG độ dài, vị trí
    j trong mọi nhóm là cùng một "khe" ngôn ngữ (thứ tự ngôn ngữ cố định
    của game). Trả list độ dài L: mã ngôn ngữ của từng vị trí, hoặc None
    khi vị trí đó không đủ bằng chứng mạnh (cần >= min_votes phiếu mạnh
    và phe thắng >= min_share). Chỉ dùng phiếu 'strong' để học — phiếu
    yếu (mặc định en) mà đem học thì sẽ học sai cả vị trí thành en.

    Ngưỡng min_share = 0.60 (không phải 0.75): các ngôn ngữ gần nhau
    (Đan Mạch/Na Uy/Thụy Điển, Séc/Slovak) chia nhau phiếu mạnh nên phe
    thắng ở khe của chúng chỉ đạt ~0.63-0.67; ngưỡng 0.75 bỏ qua cả khe
    đó, chuỗi rơi về mặc định en và làm bẩn file en. Ngưỡng 0.60 vẫn
    yêu cầu phe thắng chiếm đa số tuyệt đối phiếu mạnh; khe Na Uy học
    thành 'sv' (sai ngôn ngữ nhưng NHẤT QUÁN, không lọt vào en) — ghi
    rõ là giới hạn đã biết.
    """
    if not groups:
        return []
    width = len(groups[0])
    out: list = []
    for j in range(width):
        votes: Dict[str, int] = {}
        for g in groups:
            if j >= len(g):
                continue
            code, conf = detect_language(g[j])
            if conf == "strong" and code:
                votes[code] = votes.get(code, 0) + 1
        total = sum(votes.values())
        if total >= min_votes and votes:
            top, n_top = max(votes.items(), key=lambda kv: kv[1])
            if n_top / total >= min_share:
                out.append(top)
                continue
        out.append(None)
    return out


def align_to_modal(strong_codes, modal_langs, max_combos: int = 2000):
    """Căn một nhóm NGẮN HƠN vào thứ tự vị trí modal đã học.

    strong_codes: mã mạnh (hoặc None) của từng chuỗi trong nhóm, theo thứ
    tự xuất hiện. modal_langs: mã ngôn ngữ theo vị trí modal (độ dài M).
    Nhóm ngắn hơn vì thiếu 1-3 ngôn ngữ (vd term thiếu bản dịch ở vài thứ
    tiếng) — vị trí sau chỗ thiếu bị lệch đi, nên không gán thẳng theo chỉ
    số được. Thử mọi cách xóa d = M-N vị trí modal (chỉ khi d <= 3 và số
    tổ hợp <= max_combos); một cách xóa hợp lệ khi MỌI anchor mạnh đều
    khớp mã modal ở vị trí đã căn. Trả list mã đã căn (độ dài N): vị trí
    nào MỌI cách xóa hợp lệ đều đồng ý thì gán mã đó, vị trí còn mơ hồ
    thì None (rơi về phát hiện từng chuỗi cho vị trí đó). Trả None chỉ
    khi không có cách xóa nào hợp lệ hoặc không có anchor nào (không đủ
    bằng chứng để căn dù chỉ một vị trí). Không đoán mò: vị trí mơ hồ
    không bao giờ được gán mã "đoán".
    """
    import math
    from itertools import combinations

    n = len(strong_codes)
    m = len(modal_langs)
    d = m - n
    if d <= 0 or d > 3 or n == 0:
        return None
    if math.comb(m, d) > max_combos:
        return None
    anchors = [(i, c) for i, c in enumerate(strong_codes) if c]
    if not anchors:
        return None
    good = []
    for deleted in combinations(range(m), d):
        keep = [j for j in range(m) if j not in set(deleted)]
        if all(modal_langs[keep[i]] == c for i, c in anchors):
            good.append([modal_langs[j] for j in keep])
    if not good:
        return None
    # Chỉ gán vị trí mà mọi cách xóa hợp lệ đều đồng ý; còn lại None.
    out = []
    for i in range(n):
        first = good[0][i]
        out.append(first if all(g[i] == first for g in good) else None)
    return out if any(out) else None


# Mã ngôn ngữ hợp lệ cho pipeline (để bridge/UI kiểm tra đầu vào).
SUPPORTED_CODES = frozenset(
    ["en", "zh", "ja", "ko", "ru", "uk", "es", "fr", "pt", "it", "de",
     "nl", "pl", "cs", "sk", "ro", "sv", "da", "no", "fi", "tr", "vi",
     "id", "ms", "el", "ar", "he", "th", "tl", "hu"])
