import json
import unittest

from src.react import VMCoreAnalysisStep


HARD_LOCKUP_SAMPLE = {
    "step_id": 18,
    "reasoning": "hard lockup convergence sample",
    "action": None,
    "is_conclusive": True,
    "signature_class": "hard_lockup",
    "root_cause_class": "deadlock",
    "active_hypotheses": [
        {
            "id": "H1",
            "label": "deadlock",
            "status": "leading",
            "evidence": "bt -a shows lock non-progress",
        }
    ],
    "gates": {
        "nmi_watchdog_evidence": {
            "required_for": ["hard_lockup"],
            "status": "closed",
            "evidence": "dmesg reports NMI watchdog hard lockup",
        },
        "cpu_progress_state": {
            "required_for": ["hard_lockup"],
            "status": "closed",
            "evidence": "CPU is spinning in raw_spin_lock with no forward progress",
        },
    },
    "final_diagnosis": {
        "crash_type": "hard lockup",
        "panic_string": "NMI watchdog: hard LOCKUP on cpu 7",
        "faulting_instruction": "RIP: raw_spin_lock+0x10",
        "root_cause": "CPU made no forward progress while spinning on a contended lock.",
        "detailed_analysis": "All-CPU backtraces show a stable lock wait pattern.",
        "suspect_code": {
            "file": "kernel/locking/spinlock.c",
            "function": "raw_spin_lock",
            "line": "unknown",
        },
        "evidence": [
            "hard lockup panic string present",
            "bt -a captures spinning CPU",
        ],
    },
    "fix_suggestion": "Audit lock release paths.",
    "confidence": "high",
    "additional_notes": "sample",
}


HUNG_TASK_SAMPLE = {
    "step_id": 16,
    "reasoning": "hung task convergence sample",
    "action": None,
    "is_conclusive": True,
    "signature_class": "hung_task",
    "root_cause_class": "deadlock",
    "active_hypotheses": [
        {
            "id": "H1",
            "label": "deadlock",
            "status": "leading",
            "evidence": "mutex owner tracing forms a circular wait",
        }
    ],
    "gates": {
        "blocked_task_context": {
            "required_for": ["hung_task"],
            "status": "closed",
            "evidence": "task 1234 in D state waiting on mutex 0xffff",
        },
        "wait_chain": {
            "required_for": ["hung_task"],
            "status": "closed",
            "evidence": "owner bt shows reverse dependency and circular wait",
        },
    },
    "final_diagnosis": {
        "crash_type": "hung task",
        "panic_string": "INFO: task foo:1234 blocked for more than 120 seconds",
        "faulting_instruction": "RIP: schedule_timeout+0x20",
        "root_cause": "The task is blocked in a circular mutex wait.",
        "detailed_analysis": "Blocked task and owner traces confirm deadlock.",
        "suspect_code": {
            "file": "fs/ext4/inode.c",
            "function": "ext4_writepages",
            "line": "unknown",
        },
        "evidence": [
            "hung-task detector fired",
            "mutex owner chain is circular",
        ],
    },
    "fix_suggestion": "Enforce lock ordering.",
    "confidence": "high",
    "additional_notes": "sample",
}


OOM_PANIC_SAMPLE = {
    "step_id": 14,
    "reasoning": "oom panic convergence sample",
    "action": None,
    "is_conclusive": True,
    "signature_class": "oom_panic",
    "root_cause_class": "oom_panic",
    "active_hypotheses": [
        {
            "id": "H1",
            "label": "oom_panic",
            "status": "leading",
            "evidence": "panic_on_oom path follows a global OOM snapshot",
        }
    ],
    "gates": {
        "oom_context": {
            "required_for": ["oom_panic"],
            "status": "closed",
            "evidence": "dmesg shows panic_on_oom after global OOM",
        },
        "memory_pressure": {
            "required_for": ["oom_panic"],
            "status": "closed",
            "evidence": "kmem -i confirms near-zero available memory",
        },
    },
    "final_diagnosis": {
        "crash_type": "OOM panic",
        "panic_string": "Kernel panic - not syncing: Out of memory",
        "faulting_instruction": "RIP: panic+0x33",
        "root_cause": "The kernel intentionally panicked after confirmed OOM.",
        "detailed_analysis": "Memory pressure is confirmed by dmesg and kmem -i.",
        "suspect_code": {
            "file": "mm/oom_kill.c",
            "function": "out_of_memory",
            "line": "unknown",
        },
        "evidence": [
            "OOM panic string present",
            "memory snapshot shows exhaustion",
        ],
    },
    "fix_suggestion": "Reduce memory pressure or adjust panic_on_oom.",
    "confidence": "high",
    "additional_notes": "sample",
}


class VMCoreAnalysisStepRegressionTests(unittest.TestCase):
    def _roundtrip(self, payload: dict) -> VMCoreAnalysisStep:
        model = VMCoreAnalysisStep.model_validate_json(json.dumps(payload))
        dumped = model.model_dump()
        self.assertNotIn("crash_class", dumped)
        self.assertIn("signature_class", dumped)
        self.assertIn("root_cause_class", dumped)
        return model

    def test_hard_lockup_sample_uses_new_fields(self) -> None:
        model = self._roundtrip(HARD_LOCKUP_SAMPLE)
        self.assertEqual(model.signature_class, "hard_lockup")
        self.assertEqual(model.root_cause_class, "deadlock")
        self.assertEqual(model.gates["nmi_watchdog_evidence"].status, "closed")
        self.assertEqual(model.gates["cpu_progress_state"].status, "closed")

    def test_hung_task_sample_uses_new_fields(self) -> None:
        model = self._roundtrip(HUNG_TASK_SAMPLE)
        self.assertEqual(model.signature_class, "hung_task")
        self.assertEqual(model.root_cause_class, "deadlock")
        self.assertEqual(model.gates["blocked_task_context"].status, "closed")
        self.assertEqual(model.gates["wait_chain"].status, "closed")

    def test_oom_panic_sample_uses_new_fields(self) -> None:
        model = self._roundtrip(OOM_PANIC_SAMPLE)
        self.assertEqual(model.signature_class, "oom_panic")
        self.assertEqual(model.root_cause_class, "oom_panic")
        self.assertEqual(model.gates["oom_context"].status, "closed")
        self.assertEqual(model.gates["memory_pressure"].status, "closed")

    def test_legacy_crash_class_is_migrated(self) -> None:
        payload = {
            "step_id": 2,
            "reasoning": "legacy payload",
            "action": None,
            "is_conclusive": False,
            "crash_class": "soft_lockup",
        }
        model = VMCoreAnalysisStep.model_validate(payload)
        self.assertEqual(model.signature_class, "soft_lockup")

    def test_conclusive_missing_new_gate_is_autofilled(self) -> None:
        payload = {
            "step_id": 5,
            "reasoning": "missing gate sample",
            "action": None,
            "is_conclusive": True,
            "signature_class": "divide_error",
            "final_diagnosis": {
                "crash_type": "divide error",
                "panic_string": "divide error: 0000",
                "faulting_instruction": "RIP: do_divide_error+0x1",
                "root_cause": "Zero divisor reached idiv.",
                "detailed_analysis": "The sample intentionally omits divisor_validation gate.",
                "suspect_code": {
                    "file": "kernel/traps.c",
                    "function": "do_divide_error",
                    "line": "unknown",
                },
                "evidence": ["divide error panic string present"],
            },
        }
        model = VMCoreAnalysisStep.model_validate(payload)
        self.assertEqual(model.root_cause_class, "divide_error")
        self.assertEqual(model.gates["divisor_validation"].status, "n/a")


if __name__ == "__main__":
    unittest.main()
