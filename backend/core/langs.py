"""Languages the ads can be made in: what each needs to be written, rendered, voiced and targeted.

code -> (English name, Edge-TTS voice, Google font that covers the script, right-to-left)
A language not listed still works for copy (the model writes it); it renders with the brand font + Noto Sans and
is voiced in English.
"""
from __future__ import annotations

from typing import NamedTuple


class Lang(NamedTuple):
    name: str
    voice: str
    font: str          # Google Fonts family covering the script
    rtl: bool = False


LANGS: dict[str, Lang] = {
    "en": Lang("English", "en-US-AriaNeural", "Noto Sans"),
    "hi": Lang("Hindi", "hi-IN-SwaraNeural", "Noto Sans Devanagari"),
    "mr": Lang("Marathi", "mr-IN-AarohiNeural", "Noto Sans Devanagari"),
    "gu": Lang("Gujarati", "gu-IN-DhwaniNeural", "Noto Sans Gujarati"),
    "bn": Lang("Bengali", "bn-IN-TanishaaNeural", "Noto Sans Bengali"),
    "ta": Lang("Tamil", "ta-IN-PallaviNeural", "Noto Sans Tamil"),
    "te": Lang("Telugu", "te-IN-ShrutiNeural", "Noto Sans Telugu"),
    "kn": Lang("Kannada", "kn-IN-SapnaNeural", "Noto Sans Kannada"),
    "ml": Lang("Malayalam", "ml-IN-SobhanaNeural", "Noto Sans Malayalam"),
    "pa": Lang("Punjabi", "pa-IN-OjasNeural", "Noto Sans Gurmukhi"),
    "ur": Lang("Urdu", "ur-PK-UzmaNeural", "Noto Nastaliq Urdu", rtl=True),
    "ar": Lang("Arabic", "ar-AE-FatimaNeural", "Noto Sans Arabic", rtl=True),
    "he": Lang("Hebrew", "he-IL-HilaNeural", "Noto Sans Hebrew", rtl=True),
    "fa": Lang("Persian", "fa-IR-DilaraNeural", "Noto Sans Arabic", rtl=True),
    "es": Lang("Spanish", "es-ES-ElviraNeural", "Noto Sans"),
    "fr": Lang("French", "fr-FR-DeniseNeural", "Noto Sans"),
    "de": Lang("German", "de-DE-KatjaNeural", "Noto Sans"),
    "it": Lang("Italian", "it-IT-ElsaNeural", "Noto Sans"),
    "pt": Lang("Portuguese", "pt-BR-FranciscaNeural", "Noto Sans"),
    "nl": Lang("Dutch", "nl-NL-ColetteNeural", "Noto Sans"),
    "id": Lang("Indonesian", "id-ID-GadisNeural", "Noto Sans"),
    "ms": Lang("Malay", "ms-MY-YasminNeural", "Noto Sans"),
    "tr": Lang("Turkish", "tr-TR-EmelNeural", "Noto Sans"),
    "ru": Lang("Russian", "ru-RU-SvetlanaNeural", "Noto Sans"),
    "ja": Lang("Japanese", "ja-JP-NanamiNeural", "Noto Sans JP"),
    "ko": Lang("Korean", "ko-KR-SunHiNeural", "Noto Sans KR"),
    "zh": Lang("Chinese", "zh-CN-XiaoxiaoNeural", "Noto Sans SC"),
    "th": Lang("Thai", "th-TH-PremwadeeNeural", "Noto Sans Thai"),
    "vi": Lang("Vietnamese", "vi-VN-HoaiMyNeural", "Noto Sans"),
}
# English voice per country, so a British or Indian ad doesn't sound American
EN_VOICE = {"IN": "en-IN-NeerjaNeural", "GB": "en-GB-SoniaNeural", "AU": "en-AU-NatashaNeural",
            "CA": "en-CA-ClaraNeural", "IE": "en-IE-EmilyNeural", "NZ": "en-NZ-MollyNeural", "SG": "en-SG-LunaNeural"}
NO_SPACES = {"ja", "zh", "th"}   # captions split by characters, not words


def lang(code: str) -> Lang:
    return LANGS.get((code or "en").lower()[:2], LANGS["en"])


def name(code: str) -> str:
    return LANGS[code].name if code in LANGS else code


def voice(code: str, country: str = "") -> str:
    code = (code or "en").lower()[:2]
    return EN_VOICE.get(country.upper(), LANGS["en"].voice) if code == "en" else lang(code).voice


# The few words the templates draw themselves (Meta localises its own button under the ad, not ours on the image).
# Languages not here fall back to English labels.
_CTA_KEYS = ["SHOP_NOW", "LEARN_MORE", "SIGN_UP", "ORDER_NOW", "BOOK_NOW", "GET_OFFER", "CONTACT_US", "DOWNLOAD",
             "SUBSCRIBE", "APPLY_NOW"]
_WORDS = {
    "hi": ("कोड", "आखिरी दिन", ["अभी खरीदें", "और जानें", "साइन अप करें", "अभी ऑर्डर करें", "अभी बुक करें", "ऑफ़र पाएं",
                               "संपर्क करें", "डाउनलोड करें", "सब्सक्राइब करें", "अभी आवेदन करें"]),
    "mr": ("कोड", "शेवटचा दिवस", ["आता खरेदी करा", "अधिक जाणून घ्या", "साइन अप करा", "आता ऑर्डर करा", "आता बुक करा",
                                 "ऑफर मिळवा", "संपर्क करा", "डाउनलोड करा", "सबस्क्राइब करा", "आता अर्ज करा"]),
    "ta": ("குறியீடு", "கடைசி நாள்", ["இப்போது வாங்கவும்", "மேலும் அறிக", "பதிவு செய்க", "ஆர்டர் செய்க", "முன்பதிவு செய்க",
                                     "சலுகையைப் பெறுக", "தொடர்பு கொள்க", "பதிவிறக்குக", "குழுசேர்க", "விண்ணப்பிக்கவும்"]),
    "bn": ("কোড", "শেষ দিন", ["এখনই কিনুন", "আরও জানুন", "সাইন আপ করুন", "অর্ডার করুন", "বুক করুন", "অফার নিন",
                              "যোগাযোগ করুন", "ডাউনলোড করুন", "সাবস্ক্রাইব করুন", "আবেদন করুন"]),
    "ar": ("كود", "ينتهي", ["تسوّق الآن", "اعرف المزيد", "سجّل الآن", "اطلب الآن", "احجز الآن", "احصل على العرض",
                            "تواصل معنا", "حمّل الآن", "اشترك", "قدّم الآن"]),
    "es": ("Código", "Hasta el", ["Comprar ahora", "Más información", "Registrarse", "Pedir ahora", "Reservar",
                                  "Obtener oferta", "Contáctanos", "Descargar", "Suscribirse", "Solicitar"]),
    "fr": ("Code", "Jusqu'au", ["Acheter", "En savoir plus", "S'inscrire", "Commander", "Réserver", "Profiter de l'offre",
                                "Nous contacter", "Télécharger", "S'abonner", "Postuler"]),
    "de": ("Code", "Bis", ["Jetzt kaufen", "Mehr erfahren", "Registrieren", "Jetzt bestellen", "Jetzt buchen",
                           "Angebot sichern", "Kontakt", "Herunterladen", "Abonnieren", "Jetzt bewerben"]),
    "pt": ("Código", "Até", ["Comprar agora", "Saiba mais", "Cadastre-se", "Peça agora", "Reservar", "Aproveitar oferta",
                             "Fale conosco", "Baixar", "Assinar", "Candidatar-se"]),
    "id": ("Kode", "Berakhir", ["Beli sekarang", "Selengkapnya", "Daftar", "Pesan sekarang", "Pesan", "Ambil promo",
                                "Hubungi kami", "Unduh", "Berlangganan", "Lamar sekarang"]),
}


def words(code: str) -> tuple[str, str, dict[str, str]]:
    """('Code', 'Ends', {CTA: label}) in the ad's language; English when it isn't covered."""
    w = _WORDS.get((code or "en")[:2].lower())
    return (w[0], w[1], dict(zip(_CTA_KEYS, w[2]))) if w else ("Code", "Ends", {})
