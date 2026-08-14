from __future__ import annotations

"""Short-term rotation stock pools beyond big-tech."""

from config import BIG_TECH_FALLBACK_POOL, TECH_SECTOR_TAGS


ROTATION_SECTOR_TAGS: dict[str, dict] = {
    "power": {
        "name": "电力/公用事业",
        "codes": [
            "600900", "600027", "600011", "600023", "600021", "600795",
            "600886", "600674", "600642", "600863", "601016", "601985",
            "003816", "000539", "000543", "000600", "000690", "000875",
            "000883", "000899", "001896", "002039", "002608", "002479",
            "600905", "600101", "600236", "000027",
        ],
    },
    "innovative_drug": {
        "name": "创新药/医药",
        "codes": [
            "600276", "600196", "600079", "600062", "600085", "600129",
            "600161", "600216", "600252", "600267", "600332", "600380",
            "600422", "600436", "600511", "600521", "600535", "600557",
            "600566", "600664", "600739", "600750", "000513", "000538",
            "000623", "000661", "000739", "000756", "000963", "002001",
            "002007", "002019", "002020", "002030", "002262", "002294",
            "002317", "002332", "002422", "002603", "002653", "002675",
            "002821", "002923",
        ],
    },
    "consumer": {
        "name": "消费/食品饮料",
        "codes": [
            "600519", "000858", "000568", "600809", "600779", "000596",
            "000799", "000860", "002304", "603369", "603589", "603198",
            "600887", "002557", "600882", "600597", "600600", "000729",
            "000895", "000876", "002507", "002714", "603288", "600872",
            "000333", "000651", "600690", "600839", "002032", "002508",
            "603517", "603816", "002291", "002568",
        ],
    },
    "coal_oil_gas": {
        "name": "煤炭/油气",
        "codes": [
            "601088", "601225", "600188", "600348", "600546", "600985",
            "600123", "601001", "601699", "600256", "600508", "000983",
            "000937", "000723", "000552", "600028", "601857", "600938",
            "600339", "600583", "601808", "600968",
        ],
    },
    "nonferrous": {
        "name": "有色/黄金",
        "codes": [
            "601899", "600547", "600489", "000975", "002155", "600988",
            "601600", "600362", "000630", "000807", "000831", "002466",
            "002460", "600111", "600392", "600549", "600497", "603993",
            "000878", "000933", "000960", "000975",
        ],
    },
    "bank": {
        "name": "银行",
        "codes": [
            "601398", "601939", "601288", "601988", "601658", "600036",
            "601328", "601166", "600000", "601169", "601998", "601818",
            "601229", "601009", "600919", "601077", "002142", "000001",
            "600926", "600015", "601838", "601860", "601916", "601997",
        ],
    },
    "broker_insurance": {
        "name": "证券/保险",
        "codes": [
            "600030", "600837", "601688", "601211", "601995", "601881",
            "601066", "601377", "600999", "601901", "000776", "000166",
            "002736", "600958", "601788", "601878", "601236", "601696",
            "601318", "601628", "601601", "601336", "601319",
        ],
    },
    "real_estate_chain": {
        "name": "地产链",
        "codes": [
            "000002", "001979", "600048", "600383", "600606", "600663",
            "600325", "600266", "000069", "000402", "000031", "002244",
            "601155", "600177", "000656", "002146", "600801", "000877",
            "002271", "002372", "603737", "603816",
        ],
    },
    "infrastructure": {
        "name": "基建/中字头",
        "codes": [
            "601668", "601390", "601186", "601800", "601669", "601618",
            "601868", "601117", "601611", "601598", "601600", "601766",
            "601989", "601881", "600039", "600970", "600820", "000090",
            "002051", "002062", "002060",
        ],
    },
    "military": {
        "name": "军工",
        "codes": [
            "600760", "600893", "600316", "600372", "600879", "600765",
            "600038", "600118", "600150", "601989", "601698", "000768",
            "000738", "000733", "000519", "002013", "002025", "002179",
            "002465", "002389", "002414",
        ],
    },
    "chemical": {
        "name": "化工/材料",
        "codes": [
            "600309", "600426", "600989", "600352", "600143", "600596",
            "600486", "600409", "600810", "601233", "000301", "000703",
            "000792", "002001", "002092", "002258", "002326", "002493",
            "002648", "002683", "002812", "002895",
        ],
    },
    "agriculture": {
        "name": "农业/养殖",
        "codes": [
            "000876", "002714", "002311", "002385", "002567", "002299",
            "002124", "002041", "000998", "600598", "600737", "600371",
            "600313", "600201", "600195", "000713", "000930", "000895",
        ],
    },
}

NON_TECH_UNIVERSES = set(ROTATION_SECTOR_TAGS.keys())


def _clean_code(code: str) -> str:
    return str(code).strip().zfill(6)


def _allowed_board(code: str, *, include_chinext: bool = False, include_star: bool = False) -> bool:
    if not include_star and code.startswith(("688", "689")):
        return False
    if not include_chinext and code.startswith(("300", "301")):
        return False
    if code.startswith(("4", "8")):
        return False
    return len(code) == 6 and code.isdigit()


def get_rotation_codes(
    universe: str = "rotation",
    *,
    include_chinext: bool = False,
    include_star: bool = False,
) -> set[str]:
    universe = universe or "rotation"
    codes: set[str] = set()
    if universe in ("tech", "all", "mainline", "balanced"):
        codes.update(_clean_code(code) for code in BIG_TECH_FALLBACK_POOL)
        for info in TECH_SECTOR_TAGS.values():
            codes.update(_clean_code(code) for code in info.get("codes", []))
    if universe in ("rotation", "all", "mainline", "balanced", "non_tech_leader"):
        for info in ROTATION_SECTOR_TAGS.values():
            codes.update(_clean_code(code) for code in info.get("codes", []))
    if universe in ROTATION_SECTOR_TAGS:
        codes.update(_clean_code(code) for code in ROTATION_SECTOR_TAGS[universe].get("codes", []))
    return {
        code for code in codes
        if _allowed_board(code, include_chinext=include_chinext, include_star=include_star)
    }


def get_rotation_sector_names(code: str) -> list[str]:
    code = _clean_code(code)
    names = [
        info["name"]
        for info in ROTATION_SECTOR_TAGS.values()
        if code in {_clean_code(item) for item in info.get("codes", [])}
    ]
    return names


def iter_rotation_sectors() -> list[dict]:
    return [
        {
            "key": key,
            "name": info["name"],
            "codes": [_clean_code(code) for code in info.get("codes", [])],
        }
        for key, info in ROTATION_SECTOR_TAGS.items()
    ]
