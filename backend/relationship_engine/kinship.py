"""Kinship & Surname Analysis Engine — Soyadi ve Akraba Esleme Motoru.

Hedef kisi ile candidate (aday) agi arasindaki soyadi, bilesik/kizlik soyadi,
biyografi akrabalik beyanlari ve kullanici adi koklerini analiz ederek aile
ve yakin akraba iliskilerini yuksek guvenilirlikle tespit eder.
"""

import re
from typing import Any

from .config import WEIGHTS
from .person import Person, PersonRegistry
from .verification import classify_by_probability


TURKISH_MAP = str.maketrans('çğöşüÇĞÖŞÜ', 'cgosuCGOSU')

NOISE_TOKENS = {
    # Unvanlar & Meslekler
    'dr', 'av', 'avukat', 'prof', 'doc', 'dt', 'uzm', 'psk', 'dyt', 'ing',
    'muhendis', 'mimari', 'kuafor', 'ogretmen', 'hoca', 'baskan', 'eczaci',
    # Hesap tipleri & Genel terimler
    'official', 'resmi', 'hesap', 'fan', 'butik', 'tasarim', 'foto', 'photo',
    'photography', 'art', 'design', 'the', 'real', 'team', 'page', 'club',
    'studio', 'media', 'inc', 'agency', 'account', 'daily', 'life', 'admin',
    'contact', 'info', 'vip', 'center', 'merkez', 'group', 'grup', 'holding',
    'insaat', 'gayrimenkul', 'emlak', 'co', 'org', 'com', 'net', 'tr',
    # Lokasyonlar
    'turkey', 'turkiye', 'istanbul', 'ankara', 'izmir', 'bursa', 'antalya', 'adana',
    # Doldurma kelimeleri
    'love', 'life', 'live', 'world', 'city', 'blog', 'vlog', 'tv', 'radio',
}

TURKISH_KINSHIP_KEYWORDS = {
    'kardes': 'Kardeş',
    'kardesim': 'Kardeş',
    'kardesi': 'Kardeş',
    'abi': 'Ağabey',
    'abim': 'Ağabey',
    'abisi': 'Ağabey',
    'agabey': 'Ağabey',
    'abla': 'Abla',
    'ablam': 'Abla',
    'ablasi': 'Abla',
    'kuzen': 'Kuzen',
    'kuzenim': 'Kuzen',
    'kuzeni': 'Kuzen',
    'yegen': 'Yeğen',
    'yegenim': 'Yeğen',
    'baba': 'Baba',
    'babam': 'Baba',
    'babasi': 'Baba',
    'anne': 'Anne',
    'annem': 'Anne',
    'annesi': 'Anne',
    'valide': 'Anne',
    'ogul': 'Oğul',
    'oglum': 'Oğul',
    'kiz': 'Kız Evlat',
    'kizim': 'Kız Evlat',
    'aile': 'Aile',
    'ailem': 'Aile',
    'ailesi': 'Aile',
    'esim': 'Eş',
    'koca': 'Eş / Koca',
    'kocam': 'Eş / Koca',
    'karim': 'Eş / Kadın',
    'gelin': 'Gelin',
    'damat': 'Damat',
    'kayin': 'Kayın',
    'amca': 'Amca',
    'amcam': 'Amca',
    'dayi': 'Dayı',
    'dayim': 'Dayı',
    'hala': 'Hala',
    'halam': 'Hala',
    'teyze': 'Teyze',
    'teyzem': 'Teyze',
    'ikiz': 'İkiz',
    'ikizim': 'İkiz',
}

ENGLISH_KINSHIP_KEYWORDS = {
    'brother': 'Brother',
    'bro': 'Brother',
    'sister': 'Sister',
    'sis': 'Sister',
    'sibling': 'Sibling',
    'siblings': 'Siblings',
    'cousin': 'Cousin',
    'father': 'Father',
    'dad': 'Father',
    'mother': 'Mother',
    'mom': 'Mother',
    'son': 'Son',
    'daughter': 'Daughter',
    'family': 'Family',
    'husband': 'Husband',
    'wife': 'Wife',
    'nephew': 'Nephew',
    'niece': 'Niece',
    'uncle': 'Uncle',
    'aunt': 'Aunt',
    'twin': 'Twin',
}

ALL_KINSHIP_KEYWORDS = {**TURKISH_KINSHIP_KEYWORDS, **ENGLISH_KINSHIP_KEYWORDS}


def normalize_text(text: str | None) -> str:
    """Turkce harfleri duzlestir, ozel karakterleri temizle, kucuk harf ASCII yap."""
    if not text or not isinstance(text, str):
        return ''
    # Buyuk I/İ ve kucuk ı donusumu
    s = text.replace('İ', 'i').replace('I', 'i').replace('ı', 'i')
    s = s.translate(TURKISH_MAP).lower()
    # Harf ve rakam disindakileri bosluk yap
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    return ' '.join(s.split())


def extract_name_tokens(full_name: str | None) -> list[str]:
    """Isim alanindan anlamli token'lari cikar."""
    norm = normalize_text(full_name)
    if not norm:
        return []
    tokens = [t for t in norm.split() if len(t) >= 2 and not t.isdigit() and t not in NOISE_TOKENS]
    return tokens


def extract_username_tokens(username: str | None) -> list[str]:
    """Kullanici adini ayirici ve sayilardan bolup temiz token listesi cikar."""
    if not username or not isinstance(username, str):
        return []
    norm = normalize_text(username)
    # Nokta, altcizgi, tire ve sayilara gore bol
    parts = re.split(r'[^a-z]+', norm)
    tokens = [p for p in parts if len(p) >= 3 and p not in NOISE_TOKENS]
    return tokens


def extract_target_profile(arts: Any, target_username: str, target_intel: dict | None = None) -> dict:
    """Target hakkindaki isim, soyadi, biyografi ve kullanici adi verilerini birlestir."""
    full_name = None
    bio = None
    pk = None

    if target_intel:
        identity = target_intel.get('identity') or {}
        profile = target_intel.get('profile') or {}
        full_name = identity.get('full_name') or profile.get('full_name')
        bio = identity.get('biography') or profile.get('biography')
        pk = identity.get('pk') or target_intel.get('pk')

    if not full_name and arts:
        ti = arts.get('target_internal') or {}
        ssr = ti.get('html_ssr') or {}
        user = ti.get('user') or {}
        full_name = (ssr.get('full_name') or user.get('full_name')
                     or ti.get('full_name') or (ti.get('hidden_persona') or {}).get('full_name'))
        if not bio:
            bio = ssr.get('biography') or user.get('biography')
        if not pk:
            pk = ti.get('target_pk') or ti.get('pk')

    if not full_name and arts:
        ci = arts.get('critical_intel') or {}
        pi = arts.get('presence_intel') or {}
        full_name = (ci.get('full_name') or (ci.get('identity') or {}).get('full_name')
                     or pi.get('full_name'))
        if not bio:
            bio = ci.get('biography') or pi.get('biography')

    fn_tokens = extract_name_tokens(full_name)
    un_tokens = extract_username_tokens(target_username)

    primary_surname = None
    compound_surname = None
    if len(fn_tokens) >= 2:
        primary_surname = fn_tokens[-1]
        if len(fn_tokens) >= 3:
            compound_surname = fn_tokens[-2]
    elif len(fn_tokens) == 1:
        primary_surname = fn_tokens[0]

    # Eger full_name yoksa ve kullanici adi 2 veya daha fazla token iceriyorsa
    if not primary_surname and len(un_tokens) >= 2:
        primary_surname = un_tokens[-1]
        compound_surname = un_tokens[-2]

    return {
        'username': target_username,
        'pk': pk,
        'full_name': full_name,
        'biography': bio,
        'name_tokens': fn_tokens,
        'primary_surname': primary_surname,
        'compound_surname': compound_surname,
        'username_tokens': un_tokens,
    }


def find_bio_kinship_clue(target_bio: str | None, cand_username: str | None) -> str | None:
    """Hedef biyografisinde aday kullanici adina yonelik akraba ifadesi ara."""
    if not target_bio or not cand_username:
        return None
    bio_norm = normalize_text(target_bio)
    u_norm = normalize_text(cand_username)
    if not u_norm or u_norm not in bio_norm:
        return None

    # Kelimeler icinde u_norm ve akrabalik anahtar kelimelerinin mesafesini olc
    words = bio_norm.split()
    cand_indices = [i for i, w in enumerate(words) if u_norm in w]
    for idx in cand_indices:
        # +/- 3 kelime cevresine bak
        start = max(0, idx - 3)
        end = min(len(words), idx + 4)
        window = words[start:end]
        for w in window:
            if w in ALL_KINSHIP_KEYWORDS:
                return f"{ALL_KINSHIP_KEYWORDS[w]} ({w})"
    return None


def evaluate_candidate(person: Person, target_data: dict) -> dict | None:
    """Tek bir adayi hedef verisine gore akrabalik ve soyadi acisindan degerlendir."""
    # Hedefin kendisini atla
    if target_data.get('pk') and str(person.pk) == str(target_data.get('pk')):
        return None
    if person.username and target_data.get('username'):
        if person.username.lower() == target_data.get('username').lower():
            return None

    cand_fn_tokens = extract_name_tokens(person.full_name)
    cand_un_tokens = extract_username_tokens(person.username)
    cand_un_norm = normalize_text(person.username)

    t_primary = target_data.get('primary_surname')
    t_compound = target_data.get('compound_surname')
    t_bio = target_data.get('biography')
    t_un_tokens = target_data.get('username_tokens') or []

    # 1. Biyografide Acik Akraba Beyani
    bio_clue = find_bio_kinship_clue(t_bio, person.username)
    if bio_clue:
        weight = WEIGHTS.get('kinship_bio_declaration', 45)
        return {
            'match_type': 'bio_declaration',
            'matched_token': bio_clue,
            'confidence': 'very_high',
            'weight': weight,
            'target_surname': t_primary,
            'candidate_surname': cand_fn_tokens[-1] if cand_fn_tokens else None,
            'description': f"Hedef biyografisinde açık akraba beyanı bulundu: '{bio_clue}'",
        }

    # Soyadi kontrolleri
    if not t_primary or len(t_primary) < 3 or t_primary in NOISE_TOKENS:
        # Eger hedefin bilinen soyadi yoksa hedef un_tokens kontrol et
        if cand_fn_tokens and len(cand_fn_tokens) >= 2:
            cand_last = cand_fn_tokens[-1]
            if len(cand_last) >= 4 and cand_last not in NOISE_TOKENS:
                for tok in t_un_tokens:
                    if len(tok) >= 4 and tok == cand_last:
                        weight = WEIGHTS.get('kinship_username_stem', 10)
                        return {
                            'match_type': 'username_stem',
                            'matched_token': tok,
                            'confidence': 'medium',
                            'weight': weight,
                            'target_surname': tok,
                            'candidate_surname': cand_last,
                            'description': f"Hedef kullanıcı adı kökü ile aday soyadı eşleşti: '{tok.upper()}'",
                        }
        return None

    cand_primary = cand_fn_tokens[-1] if cand_fn_tokens else None
    cand_compound = cand_fn_tokens[-2] if len(cand_fn_tokens) >= 3 else None

    # 2. Tam Soyadi Eslesmesi (Exact Surname Match)
    if cand_primary and len(cand_primary) >= 3 and cand_primary not in NOISE_TOKENS:
        if cand_primary == t_primary:
            weight = WEIGHTS.get('kinship_exact_surname', 35)
            return {
                'match_type': 'exact_surname',
                'matched_token': t_primary,
                'confidence': 'very_high',
                'weight': weight,
                'target_surname': t_primary,
                'candidate_surname': cand_primary,
                'description': f"Hedef kişi ile tam aynı soyadı taşıyor: '{t_primary.upper()}'",
            }

    # 3. Bilesik / Kizlik Soyadi Eslesmesi (Compound / Maiden Surname Match)
    matched_compound = None
    if cand_compound and cand_compound == t_primary:
        matched_compound = t_primary
    elif t_compound and cand_primary and cand_primary == t_compound:
        matched_compound = t_compound
    elif t_compound and cand_compound and cand_compound == t_compound:
        matched_compound = t_compound

    if matched_compound and len(matched_compound) >= 3 and matched_compound not in NOISE_TOKENS:
        weight = WEIGHTS.get('kinship_compound_surname', 22)
        return {
            'match_type': 'compound_surname',
            'matched_token': matched_compound,
            'confidence': 'high',
            'weight': weight,
            'target_surname': t_primary,
            'candidate_surname': cand_primary or cand_compound,
            'description': f"Bileşik / Kızlık soyadı eşleşmesi tespit edildi: '{matched_compound.upper()}'",
        }

    # 4. Kullanici Adinda Soyadi Eslesmesi (Username Surname Match)
    # Adayin kullanici adi hedef soyadini belirgin bir token veya parca olarak iceriyor mu?
    if t_primary in cand_un_tokens:
        weight = WEIGHTS.get('kinship_username_surname', 14)
        return {
            'match_type': 'username_surname',
            'matched_token': t_primary,
            'confidence': 'medium_high',
            'weight': weight,
            'target_surname': t_primary,
            'candidate_surname': cand_primary,
            'description': f"Adayın kullanıcı adı hedef soyadını içeriyor: '@{person.username}' içinde '{t_primary}'",
        }

    if len(t_primary) >= 4 and cand_un_norm and (
        cand_un_norm.startswith(t_primary) or cand_un_norm.endswith(t_primary)
    ):
        weight = WEIGHTS.get('kinship_username_surname', 14)
        return {
            'match_type': 'username_surname',
            'matched_token': t_primary,
            'confidence': 'medium',
            'weight': weight,
            'target_surname': t_primary,
            'candidate_surname': cand_primary,
            'description': f"Aday kullanıcı adında soyadı kökü: '@{person.username}' -> '{t_primary}'",
        }

    return None


def analyze(registry: PersonRegistry, target_username: str,
            target_intel: dict | None = None, arts: Any = None) -> dict:
    """Tum registry adaylarini hedef profili ile karsilastirip akrabalik sinyallerini isle."""
    target_data = extract_target_profile(arts, target_username, target_intel)
    matches = []
    matches_by_type = {}
    total_evaluated = 0

    for person in registry:
        total_evaluated += 1
        result = evaluate_candidate(person, target_data)
        if not result:
            continue

        match_type = result['match_type']
        weight = result['weight']
        confidence = result['confidence']

        # Person nesnesini guncelle
        person.kinship_match = True
        person.kinship_type = match_type
        person.kinship_detail = result

        # Kanit kaydini ekle
        person.add_evidence(f'kinship_{match_type}', weight, result)

        # Model olasılık ve hop sınıfını yükselt
        if person.hop_class == 'unknown':
            if confidence == 'very_high':
                person.probability_1hop = max(float(person.probability_1hop or 0.0), 65.0)
            elif confidence == 'high':
                person.probability_1hop = max(float(person.probability_1hop or 0.0), 50.0)
            else:
                person.probability_1hop = max(float(person.probability_1hop or 0.0), 35.0)
            person.hop_class = classify_by_probability(person.probability_1hop / 100.0)
        else:
            if confidence == 'very_high':
                person.probability_1hop = min(99.0, max(float(person.probability_1hop or 0.0) + 15.0, 75.0))
            elif confidence == 'high':
                person.probability_1hop = min(95.0, max(float(person.probability_1hop or 0.0) + 10.0, 60.0))
            elif confidence == 'medium_high':
                person.probability_1hop = min(90.0, max(float(person.probability_1hop or 0.0) + 6.0, 45.0))
            person.hop_class = classify_by_probability(person.probability_1hop / 100.0)

        person.score_valid = True

        matches_by_type[match_type] = matches_by_type.get(match_type, 0) + 1
        matches.append({
            'pk': person.pk,
            'username': person.username,
            'full_name': person.full_name,
            'match_type': match_type,
            'matched_token': result.get('matched_token'),
            'confidence': confidence,
            'weight': weight,
            'description': result.get('description'),
        })

    return {
        'target_username': target_username,
        'target_full_name': target_data.get('full_name'),
        'target_primary_surname': target_data.get('primary_surname'),
        'target_compound_surname': target_data.get('compound_surname'),
        'target_all_name_tokens': target_data.get('name_tokens', []),
        'total_evaluated': total_evaluated,
        'total_matches': len(matches),
        'matches_by_type': matches_by_type,
        'matches': matches,
    }
