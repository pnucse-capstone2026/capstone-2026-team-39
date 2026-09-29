"""Read-only per-source explanation for selected frozen rejection cases."""
from pathlib import Path
import diagnose as d
from author_observations import PACKET, PACKET_SHA


def inspect():
    if d.sha(PACKET) != PACKET_SHA:
        raise ValueError("packet_changed")
    pins = d.checked_inputs()
    api = d.frozen.api
    selected = {"shadow_emp_04": [6], "shadow_adm_06": [5],
                "shadow_sup_04": [7], "shadow_core_04": [1, 2]}
    rows = []
    for item in d.read(PACKET)["items"]:
        if item["case_id"] not in selected:
            continue
        original = d.read(item["source_answer_path"])
        sources = api.number_sources(original["evaluation_trace"]["retrieval_stages"]["final_contexts"])
        for claim in item["claims"]:
            if claim["supported"]:
                continue
            for number in selected[item["case_id"]]:
                source = sources[number - 1]
                text, scope = api.result_source_text(source), api._result_scope_text(source)
                values = api.extract_critical_values(claim["text"])
                facts, missing = api._critical_value_support(claim["text"], values, source)
                rows.append({"case_id": item["case_id"], "claim_index": claim["claim_index"],
                             "source_number": number, "chunk_id": source["chunk_id"],
                             "claim": claim["text"], "aggregate_reason": claim["validation_reason"],
                             "aggregate_missing_values": claim["missing_critical_values"],
                             "claim_values": sorted(values), "source_critical_ok": facts,
                             "source_missing_values": sorted(missing),
                             "source_semantic_mismatch": api._semantic_relation_mismatch(claim["text"], text, source_scope=scope),
                             "isolated_source_reason": api.attribute_claim(claim["text"], [source])["validation_reason"]})
    if any(d.sha(p) != h for p, h in pins.items()):
        raise ValueError("frozen_input_changed")
    return rows, pins


if __name__ == "__main__":
    rows, pins = inspect()
    pins.update({str(PACKET): PACKET_SHA, str(Path(__file__)): d.sha(__file__)})
    d.publish(d.BASE / "source-mechanics-v1", {"per-source.json": rows, "input-sha256.json": pins,
              "limitations.txt": "Selected development cases only. Semantic flags are existing heuristic outputs, not truth labels. Isolated-source attribution changes rank to 1; compare only reason, not confidence.\n"})
