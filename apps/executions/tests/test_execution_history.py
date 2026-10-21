from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.executions import history, services
from apps.executions.models import StepOutcome
from apps.projects.models import Project, ProjectMembership
from apps.testcases.models import TestCase as CaseModel, TestStep

User = get_user_model()

PASSWORD = "uma-senha-bem-segura-123"


def make_user(email, role=Role.TESTER):
    return User.objects.create_user(
        email=email, name="Usuário de Teste", password=PASSWORD, role=role
    )


def make_project(name="Projeto Alpha"):
    return Project.objects.create(name=name, description="Descrição do projeto")


def assign(project, user):
    return ProjectMembership.objects.create(project=project, user=user)


def make_case(project, title="Caso de Teste"):
    return CaseModel.objects.create(
        project=project,
        title=title,
        description="Descrição do caso",
        preconditions="Nenhuma pré-condição",
    )


def make_step(case, position, action="Ação", expected_result="Esperado"):
    return TestStep.objects.create(
        test_case=case,
        position=position,
        action=action,
        expected_result=expected_result,
    )


class HistoryQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        cls.other_project = make_project("Projeto Beta")
        assign(cls.project, cls.owner)
        assign(cls.other_project, cls.owner)
        cls.case = make_case(cls.project, "Caso Alpha")
        make_step(cls.case, 1)
        cls.other_case = make_case(cls.other_project, "Caso Beta")
        make_step(cls.other_case, 1)

    def _complete(self, project, case):
        execution = services.start_execution(
            actor=self.owner, project=project, case_ids=[case.pk]
        )
        services.complete_execution(execution=execution, actor=self.owner)
        return execution

    def test_only_completed_executions_are_listed(self):
        completed = self._complete(self.project, self.case)
        services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )

        summaries = history.completed_execution_summaries(self.project)

        self.assertEqual([s.execution.pk for s in summaries], [completed.pk])

    def test_history_never_mixes_projects(self):
        mine = self._complete(self.project, self.case)
        self._complete(self.other_project, self.other_case)

        summaries = history.completed_execution_summaries(self.project)

        self.assertEqual([s.execution.pk for s in summaries], [mine.pk])
        self.assertEqual(summaries[0].case_ids, {self.case.pk})

    def test_summary_exposes_dates_metrics_and_case_set(self):
        execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case.pk]
        )
        execution_case = execution.execution_cases.get()
        services.update_step_result(
            execution=execution,
            step=execution_case.steps.get(),
            actor=self.owner,
            outcome=StepOutcome.PASSED,
        )
        services.complete_execution(execution=execution, actor=self.owner)

        summary = history.completed_execution_summaries(self.project)[0]

        self.assertIsNotNone(summary.completed_at)
        self.assertEqual(summary.selected_cases, 1)
        self.assertEqual(summary.approved_cases, 1)
        self.assertEqual(str(summary.success_rate), "100.00")
        self.assertEqual(summary.case_ids, {self.case.pk})
        self.assertEqual(summary.case_titles[self.case.pk], "Caso Alpha")


class HistoryComparisonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.project = make_project()
        assign(cls.project, cls.owner)
        cls.case_a = make_case(cls.project, "Caso A")
        cls.case_b = make_case(cls.project, "Caso B")
        make_step(cls.case_a, 1)
        make_step(cls.case_b, 1)

    def _complete(self, *cases):
        execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[c.pk for c in cases]
        )
        services.complete_execution(execution=execution, actor=self.owner)
        return execution

    def test_equal_case_sets_do_not_warn(self):
        self._complete(self.case_a)
        self._complete(self.case_a)

        summaries = history.completed_execution_summaries(self.project)
        comparison = history.compare_executions(summaries[1], summaries[0])

        self.assertTrue(comparison.same_case_set)
        self.assertEqual(comparison.only_in_base, ())
        self.assertEqual(comparison.only_in_against, ())
        self.assertEqual(comparison.common_count, 1)

    def test_different_case_sets_are_flagged(self):
        first = self._complete(self.case_a)
        second = self._complete(self.case_a, self.case_b)

        by_pk = {
            s.execution.pk: s
            for s in history.completed_execution_summaries(self.project)
        }
        comparison = history.compare_executions(by_pk[first.pk], by_pk[second.pk])

        self.assertFalse(comparison.same_case_set)
        self.assertEqual(comparison.common_count, 1)
        self.assertEqual(
            [difference.title for difference in comparison.only_in_against],
            ["Caso B"],
        )
        self.assertEqual(comparison.only_in_base, ())


class HistoryViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.com")
        cls.stranger = make_user("stranger@example.com")
        cls.project = make_project()
        cls.other_project = make_project("Projeto Beta")
        assign(cls.project, cls.owner)
        assign(cls.other_project, cls.owner)
        cls.case_a = make_case(cls.project, "Caso A")
        cls.case_b = make_case(cls.project, "Caso B")
        cls.foreign_case = make_case(cls.other_project, "Caso Beta")
        make_step(cls.case_a, 1)
        make_step(cls.case_b, 1)
        make_step(cls.foreign_case, 1)

    def _complete(self, project, *cases):
        execution = services.start_execution(
            actor=self.owner, project=project, case_ids=[c.pk for c in cases]
        )
        services.complete_execution(execution=execution, actor=self.owner)
        return execution

    def _list_url(self, project=None):
        return reverse(
            "executions:history_list", args=[(project or self.project).pk]
        )

    def _compare_url(self, project=None, **params):
        url = reverse(
            "executions:history_compare", args=[(project or self.project).pk]
        )
        if params:
            query = "&".join(f"{key}={value}" for key, value in params.items())
            url = f"{url}?{query}"
        return url

    def test_history_list_requires_project_access(self):
        client = Client()
        client.force_login(self.stranger)

        response = client.get(self._list_url())

        self.assertEqual(response.status_code, 404)

    def test_history_list_shows_completed_runs(self):
        self._complete(self.project, self.case_a)
        client = Client()
        client.force_login(self.owner)

        response = client.get(self._list_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Histórico de execuções")
        self.assertContains(response, "Caso A")

    def test_history_list_baseline_with_single_run(self):
        self._complete(self.project, self.case_a)
        client = Client()
        client.force_login(self.owner)

        response = client.get(self._list_url())

        self.assertContains(response, "referência inicial")
        self.assertContains(response, "não há outra execução concluída")

    def test_compare_baseline_with_single_run(self):
        execution = self._complete(self.project, self.case_a)
        client = Client()
        client.force_login(self.owner)

        response = client.get(self._compare_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "referência inicial")
        self.assertContains(response, "não há outra execução concluída")
        self.assertContains(response, f"#{execution.pk}")
        self.assertNotContains(response, "conjuntos de casos comparados são diferentes")

    def test_compare_flags_different_case_sets(self):
        self._complete(self.project, self.case_a)
        self._complete(self.project, self.case_a, self.case_b)
        client = Client()
        client.force_login(self.owner)

        response = client.get(self._compare_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "conjuntos de casos comparados são diferentes")
        self.assertContains(response, "Caso B")

    def test_compare_reports_equal_case_sets(self):
        self._complete(self.project, self.case_a)
        self._complete(self.project, self.case_a)
        client = Client()
        client.force_login(self.owner)

        response = client.get(self._compare_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "conjuntos de casos comparados são iguais")

    def test_compare_denies_unassigned_user(self):
        self._complete(self.project, self.case_a)
        self._complete(self.project, self.case_a)
        client = Client()
        client.force_login(self.stranger)

        response = client.get(self._compare_url())

        self.assertEqual(response.status_code, 404)

    def test_compare_rejects_cross_project_execution(self):
        first = self._complete(self.project, self.case_a)
        self._complete(self.project, self.case_a)
        foreign = self._complete(self.other_project, self.foreign_case)
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            self._compare_url(base=first.pk, against=foreign.pk)
        )

        self.assertEqual(response.status_code, 404)

    def test_compare_rejects_in_progress_execution(self):
        first = self._complete(self.project, self.case_a)
        self._complete(self.project, self.case_a)
        live = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case_b.pk]
        )
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            self._compare_url(base=first.pk, against=live.pk)
        )

        self.assertEqual(response.status_code, 404)

    def test_compare_rejects_selecting_the_same_execution_twice(self):
        first = self._complete(self.project, self.case_a)
        self._complete(self.project, self.case_a)
        client = Client()
        client.force_login(self.owner)

        response = client.get(
            self._compare_url(base=first.pk, against=first.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "duas execuções diferentes")
        self.assertNotContains(response, "conjuntos de casos comparados são diferentes")

    def test_compare_shows_defect_frequency(self):
        execution = services.start_execution(
            actor=self.owner, project=self.project, case_ids=[self.case_a.pk]
        )
        execution_case = execution.execution_cases.get()
        services.update_step_result(
            execution=execution,
            step=execution_case.steps.get(),
            actor=self.owner,
            outcome=StepOutcome.FAILED,
            observation="Falha ao salvar o registro.",
        )
        services.complete_execution(execution=execution, actor=self.owner)
        self._complete(self.project, self.case_a)

        client = Client()
        client.force_login(self.owner)

        response = client.get(self._compare_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Frequência de defeitos")
        self.assertContains(response, "Com observação de falha/bloqueio")
        comparison = response.context["comparison"]
        self.assertEqual(comparison.base.failed_steps, 1)
        self.assertEqual(comparison.base.failure_observations, 1)
        self.assertEqual(comparison.against.failed_steps, 0)
