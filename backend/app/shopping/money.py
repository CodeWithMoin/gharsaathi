"""The one formatter for every amount shown to a user or a model.

Amounts are integer paise everywhere in code. Tool results carry the paise
value next to the string this module produces, so the model copies "₹172"
verbatim instead of computing, rounding or spelling out a price.
"""


def format_rupees(paise: int) -> str:
    """``17200`` -> ``"₹172"``, ``124950`` -> ``"₹1,249.50"``, ``-2500`` -> ``"-₹25"``.

    Indian digit grouping; whole rupees have no decimals.
    """

    sign = "-" if paise < 0 else ""
    rupees, remainder = divmod(abs(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    fraction = f".{remainder:02d}" if remainder else ""
    return f"{sign}₹{digits}{fraction}"


def money_fields(name: str, paise: int) -> dict[str, int | str]:
    """``{"price_paise": 17200, "price": "₹172"}`` for a tool result entry."""

    return {f"{name}_paise": paise, name: format_rupees(paise)}
