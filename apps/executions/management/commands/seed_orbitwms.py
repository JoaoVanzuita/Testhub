"""Cria o cenário de demonstração OrbitWMS usado no Capítulo 4 do TCC.

O comando é idempotente: antes de recriar, remove apenas os registros que ele
mesmo gerencia (o projeto ``OrbitWMS — Gestão de Armazém`` e as contas de
demonstração com domínio ``@orbitwms.local``).

O cenário contém:

* 1 Administrador, 3 Testadores e 1 Testador inativo;
* o projeto OrbitWMS, com descrição funcional e regras de negócio;
* 5 casos de teste com 22 passos ordenados;
* 3 execuções concluídas (100%, 60% e 40%);
* 1 execução em andamento, parcialmente avaliada, para a captura do executor;
* análises de IA estáticas para as execuções concluídas (podem ser substituídas
  pelo worker real quando a integração estiver habilitada).

Uso::

    docker compose run --rm web python manage.py seed_orbitwms
    docker compose run --rm web python manage.py seed_orbitwms --no-ai

As senhas de todos os usuários de demonstração são ``teste123``.
"""

from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.accounts.services import create_user, inactivate_user
from apps.executions.models import AIStatus, Execution, Report, StepOutcome
from apps.executions.services import (
    complete_execution,
    start_execution,
    update_step_result,
)
from apps.projects.models import Project, ProjectMembership
from apps.projects.services import assign_tester, create_project
from apps.testcases.models import TestCase
from apps.testcases.services import create_case


PASSWORD = "teste123"
PROJECT_NAME = "OrbitWMS — Gestão de Armazém"

# Cronologia fixa do cenário: o projeto e os casos precisam existir antes das
# execuções. Sem isso, o ``created_at`` automático registra a data em que o
# seed foi executado e a tela do projeto mostra uma data posterior aos testes.
PROJECT_CREATED_AT = timezone.make_aware(datetime(2026, 10, 15, 9, 12, 0))
CASE_CREATED_AT = timezone.make_aware(datetime(2026, 10, 15, 10, 0, 0))
MEMBERSHIP_ASSIGNED_AT = timezone.make_aware(datetime(2026, 10, 15, 9, 20, 0))

DEMO_EMAILS = [
    "admin@orbitwms.local",
    "tester1@orbitwms.local",
    "tester2@orbitwms.local",
    "tester3@orbitwms.local",
    "inativo@orbitwms.local",
]

PROJECT_DESCRIPTION = (
    "O OrbitWMS é um sistema web de gestão de armazém (WMS) usado por centros "
    "de distribuição de médio porte. Ele cobre o ciclo completo de movimentação "
    "de mercadorias: recebimento e conferência de notas fiscais de fornecedores, "
    "endereçamento (put-away) em posições de armazenagem, separação (picking) por "
    "onda de pedidos, conferência e embalagem, expedição com emissão de etiquetas "
    "e romaneio, além de inventário cíclico rotativo. O sistema integra-se a "
    "coletores de código de barras (leitores RF), ao ERP corporativo via API REST "
    "para troca de pedidos e notas, e a transportadoras para geração de etiquetas "
    "de envio. Os perfis de acesso são operador de armazém, supervisor de "
    "logística e administrador. O armazém opera em três turnos, com meta de "
    "acuracidade de inventário acima de 99,5% e SLA de expedição de pedidos em até "
    "4 horas após o horário de corte. Regras de negócio relevantes: não é "
    "permitido endereçar um item em uma posição já ocupada; itens com controle de "
    "lote e validade exigem a leitura do lote na conferência; pedidos com "
    "prioridade 'expressa' devem ser separados primeiro na onda; e divergências "
    "de quantidade bloqueiam o fechamento da nota, exigindo aprovação do "
    "supervisor."
)


def _baseline_analysis() -> dict:
    return {
        "executive_summary": (
            "O ciclo de linha de base foi concluído com 100% de sucesso: os cinco "
            "casos e os 22 passos foram avaliados como Passou. Não houve falha, "
            "bloqueio ou passo sem avaliação."
        ),
        "insufficient_evidence": False,
        "observed_patterns": [
            {
                "kind": "observed",
                "description": (
                    "Todos os cinco casos foram aprovados e os 22 passos receberam "
                    "resultado Passou."
                ),
                "references": ["case-1/step-4", "case-2/step-4", "case-5/step-5"],
            },
            {
                "kind": "observed",
                "description": (
                    "O bloqueio de posição ocupada no endereçamento foi exercitado "
                    "e produziu o comportamento esperado."
                ),
                "references": ["case-1/step-4"],
            },
        ],
        "cause_hypotheses": [
            {
                "kind": "hypothesis",
                "description": (
                    "O ambiente de teste estava estável e as integrações "
                    "necessárias responderam dentro do esperado, o que pode "
                    "explicar a ausência de falhas."
                ),
                "references": ["case-2/step-3"],
            }
        ],
        "suggestions": [
            "Repetir o ciclo em outro ambiente para confirmar a estabilidade.",
            "Ampliar a cobertura com casos de borda e validação negativa.",
        ],
        "historical_comparison": (
            "Este é o primeiro ciclo concluído do projeto; não há execução "
            "anterior para comparação."
        ),
    }


def _regression_analysis() -> dict:
    return {
        "executive_summary": (
            "O ciclo de regressão obteve 60% de sucesso: três casos foram "
            "aprovados e dois foram reprovados. Dos 22 passos, 20 passaram e 2 "
            "falharam. As falhas se concentram no endereçamento e na separação."
        ),
        "insufficient_evidence": False,
        "observed_patterns": [
            {
                "kind": "observed",
                "description": (
                    "O endereçamento permitiu colocar um segundo item em uma "
                    "posição já ocupada, sem exibir a mensagem de bloqueio."
                ),
                "references": ["case-1/step-4"],
            },
            {
                "kind": "observed",
                "description": (
                    "A separação não vinculou o lote lido no coletor ao pedido; "
                    "o campo de lote permaneceu vazio na conferência."
                ),
                "references": ["case-5/step-4"],
            },
            {
                "kind": "observed",
                "description": (
                    "As observações registradas nos dois passos reprovados "
                    "permitem relacionar as falhas às regras de negócio de "
                    "posição ocupada e de vínculo de lote."
                ),
                "references": ["case-1/step-4", "case-5/step-4"],
            },
        ],
        "cause_hypotheses": [
            {
                "kind": "hypothesis",
                "description": (
                    "A validação de ocupação da posição pode não estar sendo "
                    "aplicada no fluxo de confirmação da movimentação."
                ),
                "references": ["case-1/step-4"],
            },
            {
                "kind": "hypothesis",
                "description": (
                    "O vínculo entre a leitura do lote no coletor e o registro do "
                    "pedido pode estar sendo perdido antes da persistência."
                ),
                "references": ["case-5/step-4"],
            },
        ],
        "suggestions": [
            "Revisar a validação de posição ocupada antes de confirmar a "
            "movimentação.",
            "Instrumentar o vínculo do lote entre a leitura no coletor e a "
            "persistência do pedido.",
            "Registrar observações nos passos reprovados para ampliar a "
            "rastreabilidade.",
        ],
        "historical_comparison": (
            "Em relação à history-1 (100%), a taxa caiu para 60% e surgiram duas "
            "falhas em casos que antes passavam. O conjunto de cinco casos foi "
            "preservado, portanto a queda não decorre de mudança de escopo."
        ),
    }


def _recurrence_analysis() -> dict:
    return {
        "executive_summary": (
            "O ciclo atual obteve 40% de sucesso: dois casos foram aprovados e "
            "três foram reprovados. Dos 22 passos, 18 passaram, 3 falharam e 1 "
            "ficou bloqueado. A falha de lote na separação é recorrente em "
            "relação ao ciclo anterior; o bloqueio de posição ocupada voltou a "
            "funcionar. A falha de impressão do romaneio tem origem externa."
        ),
        "insufficient_evidence": False,
        "observed_patterns": [
            {
                "kind": "observed",
                "description": (
                    "A falha de vínculo do lote na separação (case-5/step-4) "
                    "reaparece: o lote lido continua sem associação ao pedido."
                ),
                "references": ["case-5/step-4", "history-1/case-5/step-4"],
            },
            {
                "kind": "observed",
                "description": (
                    "O bloqueio de posição ocupada no endereçamento voltou a "
                    "funcionar: a operação foi recusada corretamente."
                ),
                "references": ["case-1/step-4"],
            },
            {
                "kind": "observed",
                "description": (
                    "O recebimento não sinalizou divergência de quantidade e "
                    "permitiu o encerramento da nota sem aprovação do supervisor."
                ),
                "references": ["case-4/step-4", "case-4/step-5"],
            },
            {
                "kind": "observed",
                "description": (
                    "A impressão do romaneio falhou por indisponibilidade da "
                    "integração com a transportadora; sem o romaneio, o despacho "
                    "ficou bloqueado."
                ),
                "references": ["case-2/step-3", "case-2/step-4"],
            },
        ],
        "cause_hypotheses": [
            {
                "kind": "hypothesis",
                "description": (
                    "O vínculo do lote pode estar sendo perdido entre a leitura "
                    "no coletor e a gravação do item separado, o que explicaria a "
                    "recorrência da falha."
                ),
                "references": ["case-5/step-4", "history-1/case-5/step-4"],
            },
            {
                "kind": "hypothesis",
                "description": (
                    "A validação de divergência de quantidade pode não estar "
                    "sendo executada antes da confirmação de encerramento da "
                    "nota."
                ),
                "references": ["case-4/step-4", "case-4/step-5"],
            },
            {
                "kind": "hypothesis",
                "description": (
                    "A falha do romaneio parece ser uma indisponibilidade da "
                    "integração externa com a transportadora, e não um defeito "
                    "funcional do fluxo de expedição."
                ),
                "references": ["case-2/step-3", "case-2/step-4"],
            },
        ],
        "suggestions": [
            "Instrumentar o vínculo do lote entre a leitura no coletor e a "
            "persistência do pedido, com teste de regressão específico.",
            "Aplicar a validação de divergência de quantidade antes de permitir "
            "o encerramento da nota.",
            "Tratar a indisponibilidade da transportadora como dependência "
            "externa, com retentativa e estado de bloqueio explícito.",
            "Manter observações em todos os passos reprovados ou bloqueados.",
        ],
        "historical_comparison": (
            "Em relação à history-1 (60%), a taxa caiu para 40%. A falha de lote "
            "na separação é recorrente; o bloqueio de posição ocupada, que havia "
            "falhado no ciclo anterior, voltou a funcionar; e surgiu uma nova "
            "falha de divergência de quantidade no recebimento. O conjunto de "
            "cinco casos foi preservado."
        ),
    }


class Command(BaseCommand):
    help = "Cria o cenário OrbitWMS usado nas capturas do Capítulo 4."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-ai",
            action="store_true",
            help="Deixa os relatórios sem análise de IA (estado Indisponível).",
        )

    def _reset(self):
        with transaction.atomic():
            Report.objects.filter(
                execution__project__name=PROJECT_NAME
            ).delete()
            Execution.objects.filter(project__name=PROJECT_NAME).delete()
            TestCase.objects.filter(project__name=PROJECT_NAME).delete()
            Project.objects.filter(name=PROJECT_NAME).delete()
            User.objects.filter(email__in=DEMO_EMAILS).delete()

    @staticmethod
    def _steps(execution_case):
        return list(execution_case.steps.order_by("snapshot_position"))

    @staticmethod
    def _case_of(execution, title):
        return execution.execution_cases.get(title=title)

    def _assess(self, execution, actor, title, outcomes, observations=None):
        observations = observations or {}
        case = self._case_of(execution, title)
        for index, (step, outcome) in enumerate(zip(self._steps(case), outcomes)):
            if outcome is None:
                continue
            update_step_result(
                execution=execution,
                step=step,
                actor=actor,
                outcome=outcome,
                observation=observations.get(index, ""),
            )

    @staticmethod
    def _set_dates(execution, *, started_at, completed_at=None):
        # Mantém também os campos automáticos na mesma cronologia, para que
        # consultas de auditoria e telas de histórico não misturem a data do
        # seed com a data simulada da execução.
        Execution.objects.filter(pk=execution.pk).update(
            started_at=started_at,
            completed_at=completed_at,
            created_at=started_at,
            updated_at=completed_at or started_at,
        )

    @staticmethod
    def _configure_report(execution, *, ai_status, analysis=None):
        report = Report.objects.get(execution=execution)
        report.ai_status = ai_status
        report.analysis = analysis
        report.save(update_fields=["ai_status", "analysis", "updated_at"])
        return report

    def handle(self, *args, **options):
        self._reset()
        self.stdout.write("Limpando dados anteriores do OrbitWMS... ok")

        create_user(
            email="admin@orbitwms.local",
            name="Administrador",
            role=Role.ADMINISTRATOR,
            password=PASSWORD,
        )
        create_user(
            email="tester1@orbitwms.local",
            name="Test1",
            role=Role.TESTER,
            password=PASSWORD,
        )
        create_user(
            email="tester2@orbitwms.local",
            name="Test2",
            role=Role.TESTER,
            password=PASSWORD,
        )
        tester3 = create_user(
            email="tester3@orbitwms.local",
            name="Test3",
            role=Role.TESTER,
            password=PASSWORD,
        )
        inativo = create_user(
            email="inativo@orbitwms.local",
            name="Testador Inativo",
            role=Role.TESTER,
            password=PASSWORD,
        )
        inactivate_user(inativo)

        project = create_project(
            name=PROJECT_NAME,
            description=PROJECT_DESCRIPTION,
        )
        Project.objects.filter(pk=project.pk).update(
            created_at=PROJECT_CREATED_AT,
            updated_at=PROJECT_CREATED_AT,
        )
        assign_tester(project, tester3)
        ProjectMembership.objects.filter(project=project).update(
            assigned_at=MEMBERSHIP_ASSIGNED_AT
        )

        # Test1 e Test2 existem apenas para a lista de usuários; não
        # participam do projeto.

        receiving = create_case(
            actor=tester3,
            project=project,
            title="Recebimento e conferência de nota fiscal",
            description=(
                "Valida a leitura da nota, a conferência dos itens e o "
                "tratamento de divergência de quantidade."
            ),
            preconditions=(
                "Nota fiscal eletrônica previamente sincronizada do ERP; "
                "coletor RF conectado e operador autenticado."
            ),
            steps=[
                {
                    "position": 1,
                    "action": (
                        "No coletor, iniciar o recebimento lendo o código de "
                        "barras da nota fiscal."
                    ),
                    "expected_result": (
                        "A nota é localizada e o resumo de itens é exibido no "
                        "coletor."
                    ),
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": (
                        "Escanear o código de cada item e informar a quantidade "
                        "recebida."
                    ),
                    "expected_result": (
                        "O sistema acumula as quantidades conferidas por item."
                    ),
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": (
                        "Nos itens com controle de lote, ler o lote e a "
                        "validade."
                    ),
                    "expected_result": (
                        "O lote e a validade ficam associados ao item conferido."
                    ),
                    "is_active": True,
                },
                {
                    "position": 4,
                    "action": (
                        "Comparar a quantidade conferida com a quantidade da "
                        "nota fiscal."
                    ),
                    "expected_result": (
                        "Divergências são sinalizadas e a nota fica pendente de "
                        "aprovação do supervisor."
                    ),
                    "is_active": True,
                },
                {
                    "position": 5,
                    "action": "Confirmar o encerramento do recebimento.",
                    "expected_result": (
                        "A nota é marcada como recebida e os itens ficam "
                        "pendentes de endereçamento."
                    ),
                    "is_active": True,
                },
            ],
        )

        putaway = create_case(
            actor=tester3,
            project=project,
            title="Endereçamento (put-away) de itens recebidos",
            description=(
                "Valida a sugestão de posição e o bloqueio de posição ocupada."
            ),
            preconditions=(
                "Há uma nota recebida com itens pendentes de endereçamento; "
                "existem posições livres no armazém."
            ),
            steps=[
                {
                    "position": 1,
                    "action": "Abrir a lista de itens pendentes de endereçamento.",
                    "expected_result": (
                        "Os itens aguardando posição são listados em ordem de "
                        "recebimento."
                    ),
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": (
                        "Selecionar um item e solicitar a sugestão de posição."
                    ),
                    "expected_result": (
                        "O sistema propõe uma posição livre compatível com o "
                        "item."
                    ),
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": (
                        "Confirmar a movimentação para a posição sugerida."
                    ),
                    "expected_result": (
                        "A posição passa a constar como ocupada pelo item e a "
                        "quantidade é atualizada."
                    ),
                    "is_active": True,
                },
                {
                    "position": 4,
                    "action": (
                        "Tentar endereçar um segundo item na mesma posição já "
                        "ocupada."
                    ),
                    "expected_result": (
                        "O sistema bloqueia a operação e exibe a mensagem de "
                        "posição ocupada."
                    ),
                    "is_active": True,
                },
            ],
        )

        picking = create_case(
            actor=tester3,
            project=project,
            title="Separação (picking) de pedido por onda",
            description=(
                "Valida a onda, a conferência de item/posição e o vínculo de "
                "lote/validade."
            ),
            preconditions=(
                "Há pedidos liberados para separação; a onda do turno foi "
                "montada."
            ),
            steps=[
                {
                    "position": 1,
                    "action": "Iniciar a onda de separação do turno.",
                    "expected_result": (
                        "A lista de pedidos e posições é ordenada pela rota de "
                        "coleta."
                    ),
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": (
                        "Escanear a posição indicada e confirmar o item a "
                        "separar."
                    ),
                    "expected_result": (
                        "O sistema valida que item e posição conferem com a "
                        "tarefa."
                    ),
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": "Informar a quantidade separada de cada item.",
                    "expected_result": (
                        "O sistema abate as quantidades do saldo da posição."
                    ),
                    "is_active": True,
                },
                {
                    "position": 4,
                    "action": "Ler o lote e a validade dos itens controlados.",
                    "expected_result": (
                        "O lote separado é vinculado ao pedido."
                    ),
                    "is_active": True,
                },
                {
                    "position": 5,
                    "action": (
                        "Finalizar a separação e enviar o pedido para "
                        "conferência."
                    ),
                    "expected_result": (
                        "O pedido avança para conferência e embalagem."
                    ),
                    "is_active": True,
                },
            ],
        )

        shipping = create_case(
            actor=tester3,
            project=project,
            title="Expedição com emissão de etiqueta e romaneio",
            description=(
                "Valida a geração do volume, da etiqueta, do romaneio e do "
                "despacho."
            ),
            preconditions=(
                "Pedido conferido e embalado, com transportadora definida."
            ),
            steps=[
                {
                    "position": 1,
                    "action": (
                        "Abrir o pedido conferido e gerar o volume de "
                        "expedição."
                    ),
                    "expected_result": (
                        "Um volume com peso e dimensões é criado para o pedido."
                    ),
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": (
                        "Emitir a etiqueta de envio da transportadora."
                    ),
                    "expected_result": (
                        "A etiqueta é gerada contendo o código de rastreio."
                    ),
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": (
                        "Imprimir o romaneio de carga da transportadora."
                    ),
                    "expected_result": (
                        "O romaneio lista os volumes e pedidos do carregamento."
                    ),
                    "is_active": True,
                },
                {
                    "position": 4,
                    "action": "Confirmar o despacho do volume.",
                    "expected_result": (
                        "O pedido é marcado como expedido e a baixa de estoque "
                        "é registrada."
                    ),
                    "is_active": True,
                },
            ],
        )

        inventory = create_case(
            actor=tester3,
            project=project,
            title="Inventário cíclico por endereço",
            description=(
                "Valida a contagem, a conferência e o ajuste de inventário."
            ),
            preconditions=(
                "O endereço indicado pelo roteiro existe e contém itens."
            ),
            steps=[
                {
                    "position": 1,
                    "action": (
                        "Iniciar a contagem do endereço indicado pelo roteiro."
                    ),
                    "expected_result": (
                        "O sistema exibe os itens esperados na posição."
                    ),
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": (
                        "Informar as quantidades contadas de cada item."
                    ),
                    "expected_result": (
                        "As quantidades contadas são registradas para o "
                        "endereço."
                    ),
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": "Submeter a contagem para conferência.",
                    "expected_result": (
                        "As divergências entre contado e esperado são "
                        "destacadas."
                    ),
                    "is_active": True,
                },
                {
                    "position": 4,
                    "action": "Aprovar o ajuste de inventário.",
                    "expected_result": (
                        "O sistema ajusta o saldo e registra o histórico da "
                        "contagem."
                    ),
                    "is_active": True,
                },
            ],
        )

        case_ids = [
            receiving.pk,
            putaway.pk,
            picking.pk,
            shipping.pk,
            inventory.pk,
        ]

        # Os casos também precisam ser anteriores às execuções. O formulário de
        # edição exibe ``updated_at``; mantê-lo na mesma cronologia evita que a
        # captura mostre uma data futura em relação aos testes.
        TestCase.objects.filter(project=project).update(
            created_at=CASE_CREATED_AT,
            updated_at=CASE_CREATED_AT,
        )

        # Execução 1: linha de base (100%).
        ex1 = start_execution(
            actor=tester3,
            project=project,
            case_ids=case_ids,
        )
        self._assess(
            ex1,
            tester3,
            "Recebimento e conferência de nota fiscal",
            [StepOutcome.PASSED] * 5,
        )
        self._assess(
            ex1,
            tester3,
            "Endereçamento (put-away) de itens recebidos",
            [StepOutcome.PASSED] * 4,
            observations={
                3: "Bloqueio de posição ocupada exibido corretamente."
            },
        )
        self._assess(
            ex1,
            tester3,
            "Separação (picking) de pedido por onda",
            [StepOutcome.PASSED] * 5,
            observations={
                0: "Lista demorou cerca de 3 s para carregar; sem impacto no resultado."
            },
        )
        self._assess(
            ex1,
            tester3,
            "Expedição com emissão de etiqueta e romaneio",
            [StepOutcome.PASSED] * 4,
        )
        self._assess(
            ex1,
            tester3,
            "Inventário cíclico por endereço",
            [StepOutcome.PASSED] * 4,
        )
        complete_execution(execution=ex1, actor=tester3)
        self._set_dates(
            ex1,
            started_at=timezone.make_aware(datetime(2026, 10, 18, 16, 55, 21)),
            completed_at=timezone.make_aware(datetime(2026, 10, 18, 17, 34, 57)),
        )

        # Execução 2: regressão no endereçamento e na separação (60%).
        ex2 = start_execution(
            actor=tester3,
            project=project,
            case_ids=case_ids,
        )
        self._assess(
            ex2,
            tester3,
            "Recebimento e conferência de nota fiscal",
            [StepOutcome.PASSED] * 5,
        )
        self._assess(
            ex2,
            tester3,
            "Endereçamento (put-away) de itens recebidos",
            [
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.FAILED,
            ],
            observations={
                3: (
                    "O sistema permitiu endereçar o segundo item na mesma "
                    "posição; os saldos foram somados e nenhuma mensagem de "
                    "bloqueio foi exibida."
                )
            },
        )
        self._assess(
            ex2,
            tester3,
            "Separação (picking) de pedido por onda",
            [
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.FAILED,
                StepOutcome.PASSED,
            ],
            observations={
                3: (
                    "O lote lido no coletor não ficou associado ao pedido; o "
                    "campo de lote permaneceu vazio na tela de conferência."
                )
            },
        )
        self._assess(
            ex2,
            tester3,
            "Expedição com emissão de etiqueta e romaneio",
            [StepOutcome.PASSED] * 4,
        )
        self._assess(
            ex2,
            tester3,
            "Inventário cíclico por endereço",
            [StepOutcome.PASSED] * 4,
        )
        complete_execution(execution=ex2, actor=tester3)
        self._set_dates(
            ex2,
            started_at=timezone.make_aware(datetime(2026, 10, 18, 17, 44, 3)),
            completed_at=timezone.make_aware(datetime(2026, 10, 18, 17, 46, 17)),
        )

        # Execução 3: recorrência da falha de lote e bloqueio externo (40%).
        ex3 = start_execution(
            actor=tester3,
            project=project,
            case_ids=case_ids,
        )
        self._assess(
            ex3,
            tester3,
            "Recebimento e conferência de nota fiscal",
            [
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.FAILED,
                StepOutcome.PASSED,
            ],
            observations={
                3: (
                    "A quantidade conferida ficou 3 unidades abaixo da "
                    "quantidade da nota, mas o sistema não sinalizou "
                    "divergência."
                ),
                4: (
                    "O encerramento foi permitido mesmo com a divergência não "
                    "tratada; nenhuma aprovação de supervisor foi exigida."
                ),
            },
        )
        self._assess(
            ex3,
            tester3,
            "Endereçamento (put-away) de itens recebidos",
            [StepOutcome.PASSED] * 4,
            observations={
                3: "O bloqueio de posição ocupada voltou a funcionar."
            },
        )
        self._assess(
            ex3,
            tester3,
            "Separação (picking) de pedido por onda",
            [
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.FAILED,
                StepOutcome.PASSED,
            ],
            observations={
                3: (
                    "Reincidência: o lote lido continua sem vínculo com o "
                    "pedido na separação."
                )
            },
        )
        self._assess(
            ex3,
            tester3,
            "Expedição com emissão de etiqueta e romaneio",
            [
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.FAILED,
                StepOutcome.BLOCKED,
            ],
            observations={
                2: (
                    "A impressão do romaneio falhou por indisponibilidade da "
                    "integração com a transportadora (erro de comunicação)."
                ),
                3: (
                    "Sem o romaneio, não foi possível confirmar o despacho."
                ),
            },
        )
        self._assess(
            ex3,
            tester3,
            "Inventário cíclico por endereço",
            [StepOutcome.PASSED] * 4,
        )
        complete_execution(execution=ex3, actor=tester3)
        self._set_dates(
            ex3,
            started_at=timezone.make_aware(datetime(2026, 10, 18, 17, 51, 16)),
            completed_at=timezone.make_aware(datetime(2026, 10, 18, 17, 54, 54)),
        )

        # Execução em andamento para a captura do executor.
        ex4 = start_execution(
            actor=tester3,
            project=project,
            case_ids=case_ids,
        )
        self._assess(
            ex4,
            tester3,
            "Recebimento e conferência de nota fiscal",
            [
                StepOutcome.PASSED,
                StepOutcome.PASSED,
                StepOutcome.FAILED,
            ],
            observations={
                2: (
                    "O lote lido não foi exibido no resumo do item; os demais "
                    "passos ainda serão avaliados."
                )
            },
        )
        self._assess(
            ex4,
            tester3,
            "Endereçamento (put-away) de itens recebidos",
            [StepOutcome.PASSED],
        )
        self._set_dates(
            ex4,
            started_at=timezone.make_aware(datetime(2026, 10, 18, 18, 10, 0)),
        )

        if not options["no_ai"]:
            self._configure_report(
                ex1,
                ai_status=AIStatus.AVAILABLE,
                analysis=_baseline_analysis(),
            )
            self._configure_report(
                ex2,
                ai_status=AIStatus.AVAILABLE,
                analysis=_regression_analysis(),
            )
            self._configure_report(
                ex3,
                ai_status=AIStatus.AVAILABLE,
                analysis=_recurrence_analysis(),
            )
        else:
            for execution in (ex1, ex2, ex3):
                self._configure_report(
                    execution,
                    ai_status=AIStatus.UNAVAILABLE,
                )

        self.stdout.write(self.style.SUCCESS("\nCenário OrbitWMS criado."))
        self.stdout.write(
            "\nUsuários (senha de todos: %s)\n"
            "  admin@orbitwms.local       Administrador\n"
            "  tester1@orbitwms.local     Testador (não vinculado)\n"
            "  tester2@orbitwms.local     Testador (não vinculado)\n"
            "  tester3@orbitwms.local     Testador (OrbitWMS)\n"
            "  inativo@orbitwms.local     Testador inativo\n" % PASSWORD
        )
        self.stdout.write(
            "\nProjeto: %s\n"
            "  Execução 1: 100%% (linha de base)\n"
            "  Execução 2: 60%% (regressão)\n"
            "  Execução 3: 40%% (recorrência)\n"
            "  Execução 4: em andamento (captura do executor)\n" % PROJECT_NAME
        )
