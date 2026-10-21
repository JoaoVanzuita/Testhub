from django import template
from django.template.defaultfilters import date as format_date

register = template.Library()


def _rows(base, against, specs):
    rows = []
    for label, attribute in specs:
        base_value = getattr(base, attribute)
        against_value = getattr(against, attribute)
        rows.append(
            {
                "label": label,
                "base": base_value,
                "against": against_value,
                "differs": base_value != against_value,
            }
        )
    return rows


@register.simple_tag
def comparison_metric_rows(comparison):
    base, against = comparison.base, comparison.against
    rows = [
        {
            "label": "Concluída em",
            "base": format_date(base.completed_at, "d/m/Y H:i"),
            "against": format_date(against.completed_at, "d/m/Y H:i"),
            "differs": False,
        }
    ]
    rows.extend(
        _rows(
            base,
            against,
            [
                ("Casos selecionados", "selected_cases"),
                ("Aprovados", "approved_cases"),
                ("Reprovados", "reproved_cases"),
                ("Pendentes", "pending_cases"),
                ("Não iniciados", "not_started_cases"),
            ],
        )
    )
    rows.append(
        {
            "label": "Taxa de sucesso",
            "base": f"{base.success_rate}%",
            "against": f"{against.success_rate}%",
            "differs": base.success_rate != against.success_rate,
        }
    )
    return rows


@register.simple_tag
def comparison_step_rows(comparison):
    return _rows(
        comparison.base,
        comparison.against,
        [
            ("Passou", "passed_steps"),
            ("Falhou", "failed_steps"),
            ("Bloqueado", "blocked_steps"),
            ("Não avaliados", "unevaluated_steps"),
            ("Com observação de falha/bloqueio", "failure_observations"),
        ],
    )
