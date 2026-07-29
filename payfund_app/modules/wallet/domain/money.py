"""Montants, soldes et devises.

**Représentation : unité mineure.** Un montant est un entier d'unités mineures de sa devise —
5 000 XOF valent `5000`, 12,50 EUR valent `1250`. C'est la seule représentation qui évite tout
flottant et tout arrondi implicite, et elle est identique à un entier de francs pour le XOF, dont
l'exposant est 0.

Cela généralise la convention du contrat API §0 (« unité entière XOF, jamais de centimes ») sans
la casser : pour le XOF, rien ne change, ni en base, ni dans l'API, ni côté client. Pour toute
autre devise, l'exposant de la table `CURRENCIES` dit combien de décimales elle porte.

Ce qui est stocké en base (`NUMERIC(14,2)`) est également l'unité mineure. Les lignes XOF
existantes restent donc valides sans aucune migration de données.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

XOF = "XOF"


@dataclass(frozen=True)
class Currency:
    code: str
    exponent: int  # nombre de décimales : 0 pour le XOF, 2 pour l'euro
    name: str


# Ajouter une devise = ajouter une ligne ici. L'exposant suit la norme ISO 4217.
CURRENCIES: dict[str, Currency] = {
    "XOF": Currency("XOF", 0, "Franc CFA BCEAO"),
    "XAF": Currency("XAF", 0, "Franc CFA BEAC"),
    "EUR": Currency("EUR", 2, "Euro"),
    "USD": Currency("USD", 2, "Dollar américain"),
    "GBP": Currency("GBP", 2, "Livre sterling"),
}


class InvalidAmount(ValueError):
    pass


class UnknownCurrency(InvalidAmount):
    pass


def devise(code: str) -> Currency:
    try:
        return CURRENCIES[code]
    except KeyError as exc:
        raise UnknownCurrency(
            f"Devise inconnue : {code!r}. Devises connues : {sorted(CURRENCIES)}."
        ) from exc


def _valider_unite_mineure(value: object, libelle: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidAmount(f"{libelle} doit être un entier d'unités mineures.")
    return value


@dataclass(frozen=True, order=True)
class Money:
    """Montant d'un mouvement. **Toujours positif** : en double entrée, c'est la `direction` de
    l'écriture qui porte le sens, jamais le signe."""

    amount: int
    currency: str = XOF

    def __post_init__(self) -> None:
        _valider_unite_mineure(self.amount, "Un montant")
        if self.amount < 0:
            raise InvalidAmount("Le montant ne peut pas être négatif.")
        devise(self.currency)

    # --- Conversions de représentation ---------------------------------------

    @property
    def exponent(self) -> int:
        return devise(self.currency).exponent

    @classmethod
    def from_major(cls, value: Decimal | int | str, currency: str = XOF) -> Money:
        """Depuis l'unité usuelle : `from_major("12.50", "EUR")` → 1250 centimes.

        Refuse toute valeur plus précise que la devise ne le permet — 12,505 € n'existe pas, et
        l'arrondir silencieusement ferait disparaître de l'argent.
        """
        exponent = devise(currency).exponent
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation as exc:
            raise InvalidAmount(f"Montant illisible : {value!r}") from exc
        mineur = decimal_value.scaleb(exponent)
        if mineur != mineur.to_integral_value():
            raise InvalidAmount(
                f"{value} a plus de {exponent} décimale(s), ce que le {currency} ne permet pas."
            )
        return cls(int(mineur), currency)

    @property
    def major_amount(self) -> Decimal:
        """Vers l'unité usuelle, pour l'affichage : 1250 centimes → `Decimal("12.50")`."""
        return Decimal(self.amount).scaleb(-self.exponent)

    @classmethod
    def from_db(cls, value: Decimal, currency: str = XOF) -> Money:
        quantized = Decimal(value).normalize()
        if quantized != quantized.to_integral_value():
            raise InvalidAmount(f"Montant fractionnaire trouvé en base : {value}")
        return cls(int(quantized), currency)

    def to_db(self) -> Decimal:
        return Decimal(self.amount)

    # --- Arithmétique --------------------------------------------------------

    def _check_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise InvalidAmount(
                f"Devises incompatibles : {self.currency} et {other.currency}."
            )

    def __add__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def convertir(self, cible: str, taux: Decimal) -> Money:
        """Applique un taux et arrondit à l'unité mineure la plus proche de la devise cible.

        Le reliquat d'arrondi n'est pas perdu : il se retrouve sur le compte de position de
        change, qui est précisément là pour l'absorber (voir `application/exchange.py`).
        """
        if taux <= 0:
            raise InvalidAmount("Un taux de change doit être strictement positif.")
        exponent_cible = devise(cible).exponent
        montant_cible = (
            self.major_amount * Decimal(taux)
        ).scaleb(exponent_cible).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return Money(int(montant_cible), cible)

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_positive(self) -> bool:
        return self.amount > 0

    def __str__(self) -> str:
        return f"{self.major_amount} {self.currency}"


@dataclass(frozen=True, order=True)
class Balance:
    """Solde d'un compte — **peut être négatif**, contrairement à `Money`.

    Un solde est une somme algébrique. Celui d'un compte suspense de passerelle est négatif par
    construction entre le dépôt d'un client et le reversement de l'opérateur (Architecture §2) ;
    celui d'un compte de position de change l'est de la même façon pendant une conversion.
    """

    amount: int
    currency: str = XOF

    def __post_init__(self) -> None:
        _valider_unite_mineure(self.amount, "Un solde")
        devise(self.currency)

    @classmethod
    def from_db(cls, value: Decimal, currency: str = XOF) -> Balance:
        quantized = Decimal(value).normalize()
        if quantized != quantized.to_integral_value():
            raise InvalidAmount(f"Solde fractionnaire trouvé en base : {value}")
        return cls(int(quantized), currency)

    @property
    def major_amount(self) -> Decimal:
        return Decimal(self.amount).scaleb(-devise(self.currency).exponent)

    def couvre(self, montant: Money) -> bool:
        if self.currency != montant.currency:
            raise InvalidAmount(
                f"Devises incompatibles : {self.currency} et {montant.currency}."
            )
        return self.amount >= montant.amount

    def __str__(self) -> str:
        return f"{self.major_amount} {self.currency}"
