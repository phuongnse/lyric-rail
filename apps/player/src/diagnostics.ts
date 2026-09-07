import contract from "../../../src/lyricrail/diagnostic_contract.json";

export function projectDiagnostic(value: string): string {
  if (value === contract.withheld || contract.messages.includes(value) || Object.values(contract.phases).includes(value)
    || Object.prototype.hasOwnProperty.call(contract.stages, value) || Object.values(contract.stages).includes(value)) return value;
  if (value.length > 16384) return contract.withheld;
  try {
    const item = JSON.parse(value) as Record<string, unknown>;
    if (!item || typeof item !== "object" || item.kind !== contract.progressKind || typeof item.phase !== "string"
      || !Object.prototype.hasOwnProperty.call(contract.phases, item.phase)) return contract.withheld;
    if (Object.keys(item).some((key) => !["kind", "phase", "message"].includes(key) && !Object.prototype.hasOwnProperty.call(contract.numericFields, key))) return contract.withheld;
    const output: Record<string, unknown> = { kind: contract.progressKind, phase: item.phase, message: contract.phases[item.phase as keyof typeof contract.phases] };
    for (const [field, maximum] of Object.entries(contract.numericFields)) {
      if (!(field in item)) continue;
      const number = item[field];
      if (typeof number !== "number" || !Number.isFinite(number) || number < 0 || number > maximum) return contract.withheld;
      output[field] = number;
    }
    return JSON.stringify(output);
  } catch { return contract.withheld; }
}
