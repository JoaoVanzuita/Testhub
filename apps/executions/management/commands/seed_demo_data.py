import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.accounts.services import create_user, inactivate_user
from apps.executions.models import (
    AIStatus,
    Execution,
    FailureCategory,
    Report,
    StepOutcome,
)
from apps.executions.services import (
    ReleaseReason,
    complete_execution,
    handoff_execution,
    release_assignment,
    start_execution,
    update_step_result,
)
from apps.projects.models import Project
from apps.projects.services import (
    assign_tester,
    create_project,
    inactivate_project,
)
from apps.testcases.models import TestCase
from apps.testcases.services import create_case


PASSWORD = "teste123"

DEMO_EMAILS = [
    "admin@teste.local",
    "ana@teste.local",
    "bruno@teste.local",
    "carla@teste.local",
    "igor.inativo@teste.local",
]

DEMO_PROJECTS = ["Loja Online", "App Mobile", "Sistema Legado"]


def _full_analysis() -> dict:
    return {
        "executive_summary": (
            "A execução transcorreu sem falhas registradas: todos os passos "
            "avaliados passaram. O fluxo principal do projeto se comportou como "
            "esperado nas condições testadas."
        ),
        "insufficient_evidence": False,
        "observed_patterns": [
            {
                "kind": "observed",
                "description": "Sequência de passos concluída na ordem definida.",
                "references": ["case-1/step-1", "case-1/step-2"],
            },
            {
                "kind": "observed",
                "description": "Nenhum passo permaneceu pendente ou não avaliado.",
                "references": ["case-2/step-1"],
            },
        ],
        "cause_hypotheses": [
            {
                "kind": "hypothesis",
                "description": (
                    "O ambiente de teste estava estável, o que pode explicar a "
                    "ausência de falhas intermitentes."
                ),
                "references": ["case-1/step-3"],
            }
        ],
        "suggestions": [
            "Ampliar a cobertura com casos de borda e validação negativa.",
            "Repetir a execução em outro ambiente para confirmar estabilidade.",
        ],
        "historical_comparison": {
            "summary": (
                "Resultado igual ou melhor que o histórico recente do projeto "
                "para os mesmos casos."
            )
        },
    }


def _inconclusive_analysis() -> dict:
    return {
        "executive_summary": (
            "Há evidências insuficientes para uma conclusão: parte dos passos "
            "ficou sem avaliação ou dependia de comportamento observado fora do "
            "escopo registrado."
        ),
        "insufficient_evidence": True,
        "observed_patterns": [],
        "cause_hypotheses": [],
        "suggestions": [
            "Avaliar todos os passos e registrar observações nos casos reprovados "
            "ou bloqueados antes de pedir uma nova análise.",
        ],
    }


class Command(BaseCommand):
    help = "Popula o banco com dados de demonstração (projetos, casos, execuções e relatórios)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Remove os dados de demonstração existentes antes de recriar (padrão: sempre limpa só os dados de demo).",
        )
        parser.add_argument(
            "--no-ai",
            action="store_true",
            help="Não configura os cenários de IA; deixa os relatórios no estado padrão (Indisponível).",
        )


    def _reset(self):
        with transaction.atomic():
            Report.objects.filter(
                execution__project__name__in=DEMO_PROJECTS
            ).delete()
            Execution.objects.filter(
                project__name__in=DEMO_PROJECTS
            ).delete()
            TestCase.objects.filter(
                project__name__in=DEMO_PROJECTS
            ).delete()
            Project.objects.filter(name__in=DEMO_PROJECTS).delete()
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
    def _configure_report(
        execution,
        *,
        ai_status,
        analysis=None,
        failure_category=None,
        in_flight=False,
    ):
        report = Report.objects.get(execution=execution)
        report.ai_status = ai_status
        report.analysis = analysis
        report.failure_category = failure_category
        if in_flight:
            report.attempts = 1
            report.lease_token = uuid.uuid4().hex
            report.lease_expires_at = timezone.now() + timedelta(hours=1)
        report.save()
        return report


    def handle(self, *args, **options):
        self._reset()
        self.stdout.write("Limpando dados de demonstração anteriores... ok")
        no_ai = options["no_ai"]

        admin = create_user(
            email="admin@teste.local",
            name="Administrador de Teste",
            role=Role.ADMINISTRATOR,
            password=PASSWORD,
        )
        ana = create_user(
            email="ana@teste.local",
            name="Ana Testadora",
            role=Role.TESTER,
            password=PASSWORD,
        )
        bruno = create_user(
            email="bruno@teste.local",
            name="Bruno Testador",
            role=Role.TESTER,
            password=PASSWORD,
        )
        carla = create_user(
            email="carla@teste.local",
            name="Carla Testadora",
            role=Role.TESTER,
            password=PASSWORD,
        )
        inativo = create_user(
            email="igor.inativo@teste.local",
            name="Igor Inativo",
            role=Role.TESTER,
            password=PASSWORD,
        )
        inactivate_user(inativo)

        loja = create_project(
            name="Loja Online",
            description="Loja virtual: login, carrinho, checkout e cupons.",
        )
        app = create_project(
            name="App Mobile",
            description="Aplicativo móvel: sincronização offline e notificações.",
        )
        legado = create_project(
            name="Sistema Legado",
            description="Sistema antigo mantido apenas para consulta histórica.",
        )

        for tester in (ana, bruno):
            assign_tester(loja, tester)
        for tester in (ana, bruno, carla):
            assign_tester(app, tester)
        assign_tester(legado, bruno)

        loja_cases = {}
        loja_cases["login"] = create_case(
            actor=ana,
            project=loja,
            title="Login com credenciais válidas",
            description="Valida o acesso ao painel com e-mail e senha corretos.",
            preconditions="Usuário ativo cadastrado no sistema.",
            steps=[
                {
                    "position": 1,
                    "action": "Abrir a tela de login.",
                    "expected_result": "Formulário de login é exibido.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Informar e-mail e senha válidos e enviar.",
                    "expected_result": "Usuário é autenticado e vê o painel.",
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": "Verificar o nome do usuário no topo.",
                    "expected_result": "Nome do usuário logado é exibido.",
                    "is_active": True,
                },
            ],
        )
        loja_cases["senha"] = create_case(
            actor=ana,
            project=loja,
            title="Recuperação de senha",
            description="Solicita e conclui a redefinição de senha por e-mail.",
            preconditions="E-mail de recuperação configurado.",
            steps=[
                {
                    "position": 1,
                    "action": "Clicar em “Esqueci minha senha”.",
                    "expected_result": "Tela de solicitação é exibida.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Informar o e-mail e enviar.",
                    "expected_result": "Mensagem de envio é exibida sem revelar se a conta existe.",
                    "is_active": True,
                },
            ],
        )
        loja_cases["carrinho"] = create_case(
            actor=ana,
            project=loja,
            title="Adicionar item ao carrinho",
            description="Inclui um produto no carrinho a partir da vitrine.",
            preconditions="Existe produto com estoque.",
            steps=[
                {
                    "position": 1,
                    "action": "Abrir a página do produto.",
                    "expected_result": "Página com preço e botão de compra é exibida.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Clicar em “Adicionar ao carrinho”.",
                    "expected_result": "Item é incluído e o contador atualiza.",
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": "Abrir o carrinho.",
                    "expected_result": "O item aparece com quantidade e subtotal.",
                    "is_active": True,
                },
                {
                    "position": 4,
                    "action": "Alterar a quantidade para 2.",
                    "expected_result": "Subtotal é recalculado.",
                    "is_active": True,
                },
            ],
        )
        loja_cases["checkout"] = create_case(
            actor=ana,
            project=loja,
            title="Finalizar compra com cartão",
            description="Conclui a compra pagando com cartão de crédito.",
            preconditions="Carrinho com ao menos um item.",
            steps=[
                {
                    "position": 1,
                    "action": "Iniciar o checkout.",
                    "expected_result": "Formulário de entrega é exibido.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Preencher os dados de entrega.",
                    "expected_result": "Dados são aceitos.",
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": "Escolher pagamento com cartão.",
                    "expected_result": "Campos do cartão são exibidos.",
                    "is_active": True,
                },
                {
                    "position": 4,
                    "action": "Confirmar o pagamento.",
                    "expected_result": "Compra é aprovada e o pedido é criado.",
                    "is_active": True,
                },
                {
                    "position": 5,
                    "action": "Verificar o e-mail de confirmação.",
                    "expected_result": "E-mail com os dados do pedido chega.",
                    "is_active": True,
                },
            ],
        )
        loja_cases["cupom"] = create_case(
            actor=ana,
            project=loja,
            title="Aplicar cupom de desconto",
            description="Aplica um cupom válido e recalcula o total.",
            preconditions="Cupom ativo e dentro da validade.",
            steps=[
                {
                    "position": 1,
                    "action": "Informar o código do cupom.",
                    "expected_result": "Cupom é reconhecido.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Confirmar a aplicação.",
                    "expected_result": "Total é reduzido conforme o desconto.",
                    "is_active": True,
                },
            ],
        )
        descontinuado = create_case(
            actor=ana,
            project=loja,
            title="Pagamento por boleto (descontinuado)",
            description="Fluxo antigo, mantido apenas para histórico.",
            preconditions="—",
            steps=[
                {
                    "position": 1,
                    "action": "Escolher boleto no checkout.",
                    "expected_result": "Boleto é gerado.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Confirmar o pagamento do boleto.",
                    "expected_result": "Pedido é marcado como pago.",
                    "is_active": True,
                },
            ],
        )
        TestCase.objects.filter(pk=descontinuado.pk).update(is_active=False)

        app_cases = {}
        app_cases["sync"] = create_case(
            actor=carla,
            project=app,
            title="Sincronizar dados offline",
            description="Sincroniza alterações feitas sem conexão.",
            preconditions="Há alterações pendentes locais.",
            steps=[
                {
                    "position": 1,
                    "action": "Ativar o modo avião e alterar um registro.",
                    "expected_result": "Alteração fica pendente localmente.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Desativar o modo avião.",
                    "expected_result": "O app inicia a sincronização.",
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": "Conferir o registro no servidor.",
                    "expected_result": "A alteração aparece no servidor.",
                    "is_active": True,
                },
            ],
        )
        app_cases["push"] = create_case(
            actor=carla,
            project=app,
            title="Receber notificação push",
            description="Recebe uma notificação e abre a tela relacionada.",
            preconditions="Notificações permitidas no dispositivo.",
            steps=[
                {
                    "position": 1,
                    "action": "Disparar uma notificação de teste.",
                    "expected_result": "Notificação aparece no dispositivo.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Tocar na notificação.",
                    "expected_result": "App abre na tela correspondente.",
                    "is_active": True,
                },
            ],
        )
        app_cases["bio"] = create_case(
            actor=carla,
            project=app,
            title="Login biométrico",
            description="Autentica usando biometria do dispositivo.",
            preconditions="Biometria cadastrada no aparelho.",
            steps=[
                {
                    "position": 1,
                    "action": "Abrir o app e escolher biometria.",
                    "expected_result": "Solicitação biométrica é exibida.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Autenticar com a digital.",
                    "expected_result": "Usuário entra no app.",
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": "Bloquear e reabrir o app.",
                    "expected_result": "Sessão permanece ativa.",
                    "is_active": True,
                },
            ],
        )

        legado_case = create_case(
            actor=bruno,
            project=legado,
            title="Emissão de relatório fiscal",
            description="Gera o relatório fiscal mensal.",
            preconditions="Competência fechada.",
            steps=[
                {
                    "position": 1,
                    "action": "Abrir o módulo de relatórios.",
                    "expected_result": "Lista de relatórios é exibida.",
                    "is_active": True,
                },
                {
                    "position": 2,
                    "action": "Selecionar a competência e gerar.",
                    "expected_result": "Relatório é gerado sem erros.",
                    "is_active": True,
                },
                {
                    "position": 3,
                    "action": "Baixar o arquivo.",
                    "expected_result": "Arquivo é baixado corretamente.",
                    "is_active": True,
                },
            ],
        )

        ex1 = start_execution(
            actor=ana,
            project=loja,
            case_ids=[loja_cases["login"].pk, loja_cases["senha"].pk, loja_cases["carrinho"].pk],
        )
        self._assess(ex1, ana, "Login com credenciais válidas", [StepOutcome.PASSED] * 3)
        self._assess(ex1, ana, "Recuperação de senha", [StepOutcome.PASSED] * 2)
        self._assess(ex1, ana, "Adicionar item ao carrinho", [StepOutcome.PASSED] * 4)
        complete_execution(execution=ex1, actor=ana)

        ex2 = start_execution(
            actor=ana,
            project=loja,
            case_ids=[loja_cases["login"].pk, loja_cases["checkout"].pk],
        )
        self._assess(ex2, ana, "Login com credenciais válidas", [StepOutcome.PASSED] * 3)
        self._assess(
            ex2,
            ana,
            "Finalizar compra com cartão",
            [StepOutcome.PASSED, StepOutcome.FAILED, StepOutcome.BLOCKED, None, StepOutcome.PASSED],
            observations={
                1: "O formulário de entrega rejeitou o CEP válido.",
                2: "Sem o CEP aceito, o pagamento não pôde ser testado.",
            },
        )
        complete_execution(execution=ex2, actor=ana)

        ex3 = start_execution(
            actor=ana,
            project=loja,
            case_ids=[loja_cases["senha"].pk, loja_cases["cupom"].pk],
        )
        self._assess(ex3, ana, "Recuperação de senha", [StepOutcome.PASSED] * 2)
        self._assess(ex3, ana, "Aplicar cupom de desconto", [StepOutcome.BLOCKED, None])
        complete_execution(execution=ex3, actor=ana)

        ex4 = start_execution(actor=ana, project=loja, case_ids=[loja_cases["carrinho"].pk])
        complete_execution(execution=ex4, actor=ana)

        ex5 = start_execution(
            actor=ana,
            project=loja,
            case_ids=[loja_cases["login"].pk, loja_cases["cupom"].pk],
        )
        self._assess(ex5, ana, "Login com credenciais válidas", [StepOutcome.PASSED] * 3)
        handoff_execution(execution=ex5, actor=ana, successor=bruno)
        self._assess(ex5, bruno, "Aplicar cupom de desconto", [StepOutcome.BLOCKED, None])
        complete_execution(execution=ex5, actor=bruno)

        ex6 = start_execution(
            actor=ana,
            project=loja,
            case_ids=[loja_cases["carrinho"].pk, loja_cases["checkout"].pk],
        )
        self._assess(ex6, ana, "Adicionar item ao carrinho", [StepOutcome.PASSED, None, None, None])

        ex7 = start_execution(
            actor=carla,
            project=app,
            case_ids=[app_cases["sync"].pk, app_cases["push"].pk],
        )
        self._assess(ex7, carla, "Sincronizar dados offline", [StepOutcome.PASSED] * 3)
        self._assess(
            ex7,
            carla,
            "Receber notificação push",
            [StepOutcome.PASSED, StepOutcome.FAILED],
            observations={1: "A notificação não abriu a tela correta em iOS."},
        )
        complete_execution(execution=ex7, actor=carla)

        ex8 = start_execution(actor=carla, project=app, case_ids=[app_cases["bio"].pk])
        self._assess(ex8, carla, "Login biométrico", [StepOutcome.PASSED, None, None])
        release_assignment(execution=ex8, actor=carla, reason=ReleaseReason.VOLUNTARY)

        ex9 = start_execution(actor=bruno, project=legado, case_ids=[legado_case.pk])
        self._assess(ex9, bruno, "Emissão de relatório fiscal", [StepOutcome.PASSED] * 3)
        complete_execution(execution=ex9, actor=bruno)

        if not no_ai:
            self._configure_report(
                ex1, ai_status=AIStatus.AVAILABLE, analysis=_full_analysis()
            )
            self._configure_report(
                ex2,
                ai_status=AIStatus.UNAVAILABLE,
                failure_category=FailureCategory.CONFIGURATION,
            )
            self._configure_report(
                ex3, ai_status=AIStatus.PROCESSING, in_flight=True
            )
            self._configure_report(ex4, ai_status=AIStatus.UNAVAILABLE)
            self._configure_report(
                ex5,
                ai_status=AIStatus.AVAILABLE,
                analysis=_inconclusive_analysis(),
            )
            self._configure_report(
                ex7, ai_status=AIStatus.AVAILABLE, analysis=_full_analysis()
            )
            self._configure_report(ex9, ai_status=AIStatus.UNAVAILABLE)

        inactivate_project(legado)

        self.stdout.write(self.style.SUCCESS("\nDados de demonstração criados."))
        self.stdout.write(
            "\nUsuários (senha de todos: %s)\n"
            "  admin@teste.local            Administrador\n"
            "  ana@teste.local              Testador  (Loja Online, App Mobile)\n"
            "  bruno@teste.local            Testador  (Loja, App Mobile, Legado)\n"
            "  carla@teste.local            Testador  (App Mobile)\n"
            "  igor.inativo@teste.local     Testador  (inativo)\n" % PASSWORD
        )
        self.stdout.write(
            "\nProjetos: Loja Online (ativo) · App Mobile (ativo) · "
            "Sistema Legado (inativo)\n"
        )
        self.stdout.write(
            "\nCenários no projeto “Loja Online”:\n"
            "  100% aprovado · com falhas · nada avaliado · transferida · "
            "em andamento\n"
            "No “App Mobile”:\n"
            "  concluída com falha · em andamento sem responsável\n"
            "No “Sistema Legado”: concluída (visível ao Administrador)\n"
        )
        self.stdout.write(
            "\nEstados de IA: Disponível (completa), Disponível (inconclusiva), "
            "Em processamento, Indisponível.\n"
        )
