"""Value objects for the ENS domain, from both of its sources.

Most of these model the Anexo II of RD 311/2022 as the ENS Navegable publishes
it: the categories, the measures, their dimensions, levels and refuerzos. The
last four — ``ArticleQuestion``, ``ArticleCheck``, ``MeasureEvidence`` and
``Guia808`` — model what an audit additionally checks and asks for, which the
site does *not* publish and comes from the CCN-STIC 808 guide instead.

Two sources, one domain: they meet on the measure code, which is what lets
``evidencias_auditoria`` and ``alcance_auditoria`` be joined by a client. What
keeps them honest is that neither half depends on where it was read from —
``snapshot.codec`` and ``guia.codec`` build these, and nothing here knows they
exist.

Every one of them is ``frozen`` **and** deeply immutable (``frozenset``,
``tuple``, never ``list`` or ``dict``). That is not decoration: these objects
are loaded once and live for the whole process, shared by every request, so a
mutable field would be shared mutable state reachable from every tool call.
``frozen=True`` alone does not give that — it stops the rebinding, not the
mutation of what is bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SecurityDimension(Enum):
    """Dimensión de seguridad a la que aplica una medida."""

    CONFIDENCIALIDAD = "confidencialidad"
    INTEGRIDAD = "integridad"
    DISPONIBILIDAD = "disponibilidad"
    AUTENTICIDAD = "autenticidad"
    TRAZABILIDAD = "trazabilidad"


class DimensionLevel(Enum):
    """Nivel de una dimensión de seguridad del sistema."""

    BAJO = "bajo"
    BASICO = "bajo"  # alias de entrada heredado; la salida usa "bajo"
    MEDIO = "medio"
    ALTO = "alto"

    @classmethod
    def _missing_(cls, value: object) -> DimensionLevel | None:
        if value == "basico":
            return cls.BAJO
        return None


class SystemCategory(Enum):
    """Categoría oficial del sistema: BÁSICA, MEDIA o ALTA."""

    BASICA = "basica"
    MEDIA = "media"
    ALTA = "alta"

    @classmethod
    def _missing_(cls, value: object) -> SystemCategory | None:
        legacy = {"basico": cls.BASICA, "bajo": cls.BASICA, "medio": cls.MEDIA, "alto": cls.ALTA}
        return legacy.get(value) if isinstance(value, str) else None


# Compatibility for clients importing the pre-0.1.1 name. New code should use
# DimensionLevel or SystemCategory according to the field it models.
ApplicabilityLevel = DimensionLevel


class CategoryGroup(Enum):
    """Los tres bloques de medidas del Anexo II."""

    MARCO_ORGANIZATIVO = "org"
    MARCO_OPERACIONAL = "op"
    MEDIDAS_PROTECCION = "mp"


@dataclass(frozen=True, slots=True)
class Category:
    """Una categoría o subcategoría de medidas (p. ej. ``mp.if``)."""

    code: str
    name: str
    group: CategoryGroup


@dataclass(frozen=True, slots=True)
class Reinforcement:
    """Un refuerzo (``R1``, ``R2``, ...) exigible en un nivel concreto.

    El Anexo II no exige "op.acc.5 en nivel medio" sino "op.acc.5 + R2 en
    nivel medio", así que el refuerzo sólo significa algo emparejado con su
    nivel: es el par completo lo que va en una Declaración de Aplicabilidad.

    ``alternative`` distingue las dos formas que usa la tabla, y confundirlas
    cambia lo que hay que implantar: ``"+ R1 + R2"`` exige **ambos**, mientras
    que ``"+ [R1 o R2 o R3 o R4]"`` —la celda real de ``op.acc.5`` en nivel
    básico— pide **uno cualquiera** de los cuatro.
    """

    code: str
    level: DimensionLevel
    # ponytail: un booleano basta porque ninguna de las 219 celdas de la tabla
    # real lleva más de un grupo de elección. Si alguna llegara a llevar dos,
    # esto no podría decir qué refuerzo pertenece a cuál — pero raw_levels
    # conserva el literal, así que el dato seguiría estando a la vista.
    alternative: bool = False
    # La redacción del refuerzo en el RD 311/2022. Viaja aparte de la tabla
    # (que sólo lo nombra) y por defecto vacía, igual que ``description`` en
    # SecurityMeasure: una fuente incompleta deja el resto de la medida intacto.
    text: str = ""


@dataclass(frozen=True, slots=True)
class AuditRequirement:
    """Una pregunta del cuestionario de auditoría (CCN-STIC 808).

    Es lo que un auditor pregunta sobre una medida. ``essential`` marca las que
    no se pueden fallar, con la consecuencia que fija la CCN-STIC 808 §5: "si
    alguno de ellos no se cumple, el auditor debe considerar la medida de
    seguridad en su conjunto como 'no implementada'".

    ``code`` es la etiqueta que imprime el sitio ("1.1"), **no** un
    identificador: dentro de una misma medida y nivel la numeración se
    reinicia, así que "1.1" aparece cinco veces en ``op.acc.5``. La
    identidad estable es ``position``, el orden en que el cuestionario los
    plantea — que es además donde se ve el reinicio.
    """

    position: int
    code: str
    level: SystemCategory
    essential: bool
    question: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class SecurityMeasure:
    """Una medida de seguridad individual (p. ej. ``mp.if.3``)."""

    code: str
    title: str
    description: str
    category_code: str
    dimensions: frozenset[SecurityDimension]
    levels: frozenset[DimensionLevel]
    # Lo que el RD 311/2022 exige de esta medida, en su propia redacción. No es
    # lo mismo que ``description``, que es el cuestionario de la CCN-STIC 808:
    # aquélla *pregunta* ("¿Se gestionan las autorizaciones...?") y ésta
    # *obliga* ("Se establecerá un proceso formal de autorizaciones que
    # cubra..."). Un ``Reinforcement`` ya llevaba su ``text`` y la medida no, así
    # que quien preguntaba por una medida recibía el examen y no la norma.
    # Aditivo y con default, como los tres de abajo: una fuente incompleta deja
    # el resto de la medida intacta.
    norm_text: str = ""
    # Un frozenset de pares (y no un dict nivel -> refuerzos) porque los campos
    # de una dataclass frozen tienen que ser hashables, igual que dimensions y
    # levels. Ambos campos son aditivos: llevan default para que construir una
    # medida sin ellos —como hace cualquier test previo a los refuerzos— siga
    # siendo válido.
    reinforcements: frozenset[Reinforcement] = frozenset()
    # Las tres celdas Bajo/Medio/Alto tal cual las sirve el sitio. Sólo hay una
    # muestra real verificada de ese vocabulario ("+ R1"), así que conservar el
    # literal es lo que impide que una forma no prevista se pierda en silencio.
    raw_levels: tuple[str, ...] = ()
    # Una tupla y no un frozenset: el orden es dato -es donde se ve el reinicio
    # de la numeración- y además tiene que ser hashable como el resto.
    audit_requirements: tuple[AuditRequirement, ...] = ()


@dataclass(frozen=True, slots=True)
class ApplicableMeasure:
    """Una línea de una Declaración de Aplicabilidad.

    No es "una medida" sino "esta medida, exigida a este sistema concreto y a
    este nivel": la misma ``op.acc.5`` pide un refuerzo cualquiera de cuatro en
    un sistema básico, y R5 además en uno medio. El nivel exigible y los
    refuerzos que lo acompañan son parte de la respuesta, no del catálogo.
    """

    measure: SecurityMeasure
    required_level: DimensionLevel
    reinforcements: frozenset[Reinforcement]


@dataclass(frozen=True, slots=True)
class ArticleQuestion:
    """Una pregunta de verificación sobre un artículo del RD 311/2022."""

    reference: str
    question: str


@dataclass(frozen=True, slots=True)
class ArticleCheck:
    """Lo que un auditor comprueba de un artículo del ENS.

    Una auditoría verifica el articulado además del Anexo II, y esa mitad no
    la publica el ENS Navegable: sale de la CCN-STIC 808 §6.1. ``evidence`` son
    los documentos que la guía propone que el auditor pida.
    """

    reference: str
    title: str
    evidence: tuple[str, ...]
    questions: tuple[ArticleQuestion, ...]


@dataclass(frozen=True, slots=True)
class MaturityLevel:
    """Un nivel de madurez CMM de los que define la CCN-STIC 808 §6.

    El código solo ("L4") no dice nada a quien lo recibe, y la guía sí lo dice:
    la escala va de L0 (inexistente) a L5 (optimizado) y cada nivel tiene su
    caracterización. Emparejar el código con su nombre es lo mismo que hace el
    resto del dominio — un ``Reinforcement`` lleva su ``text``, un
    ``AuditRequirement`` su ``question`` — en vez de servir una etiqueta que
    obliga a ir a buscar la guía para entenderla.
    """

    code: str
    name: str


@dataclass(frozen=True, slots=True)
class MeasureEvidence:
    """Los documentos que un auditor puede pedir sobre una medida (CCN-STIC 808 §6.2).

    Se une por ``measure_code`` con la ``SecurityMeasure`` del Anexo II, que
    viene de otra fuente: el sitio publica la medida, la guía dice qué papeles
    la acreditan.
    """

    measure_code: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Guia808:
    """La CCN-STIC 808 extraída: el articulado y las evidencias por medida.

    Es el otro corpus del dominio junto al Anexo II, y como aquél se modela con
    campos hashables: una tupla de ``MeasureEvidence`` y no un
    ``dict[str, tuple[str, ...]]`` porque ``frozen=True`` sólo impide reasignar
    el campo, no mutar el dict que hay dentro — y este objeto se construye una
    vez y vive lo que dura el proceso, alcanzable desde cada llamada a
    ``evidencias_auditoria``. Llega en el orden del Anexo II —el mismo que
    recorren las demás tools— y quien la sirve no la reordena.

    ``source`` es la atribución de la guía, que viaja con el dato porque lo que
    se distribuye es la extracción, no el PDF.
    """

    source: str
    articles: tuple[ArticleCheck, ...]
    measure_evidence: tuple[MeasureEvidence, ...]
