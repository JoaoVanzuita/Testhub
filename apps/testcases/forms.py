from django import forms

from .models import TestCase, TestStep


class TestCaseForm(forms.ModelForm):
    class Meta:
        model = TestCase
        fields = ["title", "description", "preconditions"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "preconditions": forms.Textarea(attrs={"rows": 3}),
        }


class TestCaseEditForm(TestCaseForm):
    class Meta(TestCaseForm.Meta):
        fields = ["title", "description", "preconditions", "is_active"]


class TestStepForm(forms.ModelForm):
    position = forms.IntegerField(
        label="Ordem",
        min_value=1,
        widget=forms.NumberInput(
            attrs={"class": "step-position", "min": 1, "inputmode": "numeric"}
        ),
    )

    class Meta:
        model = TestStep
        fields = ["position", "action", "expected_result", "is_active"]
        widgets = {
            "action": forms.Textarea(attrs={"rows": 2}),
            "expected_result": forms.Textarea(attrs={"rows": 2}),
        }


class BaseTestStepFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        submitted = []
        for form in self.forms:
            if not form.cleaned_data:
                continue
            if form.errors:
                return
            if form.cleaned_data.get("DELETE"):
                continue
            submitted.append(form.cleaned_data)

        if not submitted:
            raise forms.ValidationError("Informe ao menos um passo para o caso.")

        positions = [step["position"] for step in submitted]
        if len(set(positions)) != len(positions):
            raise forms.ValidationError("As ordens dos passos devem ser únicas.")

        if not any(step.get("is_active") for step in submitted):
            raise forms.ValidationError("O caso deve possuir ao menos um passo ativo.")


TestStepFormSet = forms.inlineformset_factory(
    TestCase,
    TestStep,
    form=TestStepForm,
    formset=BaseTestStepFormSet,
    extra=1,
    can_delete=True,
)


def extract_steps(formset):
    steps = []
    for form in formset.forms:
        if not form.cleaned_data:
            continue
        if form.cleaned_data.get("DELETE"):
            if form.instance.pk:
                steps.append({"pk": form.instance.pk, "delete": True})
            continue
        steps.append(
            {
                "pk": form.instance.pk,
                "position": form.cleaned_data["position"],
                "action": form.cleaned_data["action"],
                "expected_result": form.cleaned_data["expected_result"],
                "is_active": form.cleaned_data.get("is_active", True),
            }
        )
    return steps
