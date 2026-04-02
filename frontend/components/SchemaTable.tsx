"use client";

import { ColumnSchema } from "@/lib/api";

const TYPE_OPTIONS: ColumnSchema["detected_type"][] = [
  "numeric",
  "categorical",
  "datetime",
  "boolean",
];

interface Props {
  schema: ColumnSchema[];
  overrides: Record<string, ColumnSchema["detected_type"]>;
  onOverride: (column: string, type: ColumnSchema["detected_type"]) => void;
}

export default function SchemaTable({ schema, overrides, onOverride }: Props) {
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-gray-700">Inferred Schema</h2>
      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2.5 text-left font-medium text-gray-500">Column</th>
              <th className="px-4 py-2.5 text-left font-medium text-gray-500">Type</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {schema.map((col) => {
              const current = overrides[col.name] ?? col.detected_type;
              return (
                <tr key={col.name}>
                  <td className="px-4 py-2 font-mono text-gray-800">{col.name}</td>
                  <td className="px-4 py-2">
                    <select
                      value={current}
                      onChange={(e) =>
                        onOverride(
                          col.name,
                          e.target.value as ColumnSchema["detected_type"]
                        )
                      }
                      aria-label={`Type for ${col.name}`}
                      className="rounded border border-gray-300 bg-white px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    >
                      {TYPE_OPTIONS.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                    {overrides[col.name] && overrides[col.name] !== col.detected_type && (
                      <span className="ml-2 text-xs text-amber-600">(overridden)</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
