import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SchemaTable from "@/components/SchemaTable";
import { ColumnSchema } from "@/lib/api";

const schema: ColumnSchema[] = [
  { name: "age", detected_type: "numeric" },
  { name: "city", detected_type: "categorical" },
];

describe("SchemaTable", () => {
  it("renders all column names", () => {
    render(<SchemaTable schema={schema} overrides={{}} onOverride={jest.fn()} />);
    expect(screen.getByText("age")).toBeInTheDocument();
    expect(screen.getByText("city")).toBeInTheDocument();
  });

  it("shows detected type in select", () => {
    render(<SchemaTable schema={schema} overrides={{}} onOverride={jest.fn()} />);
    const ageSelect = screen.getByRole("combobox", { name: /type for age/i }) as HTMLSelectElement;
    expect(ageSelect.value).toBe("numeric");
  });

  it("calls onOverride when dropdown changes", async () => {
    const user = userEvent.setup();
    const onOverride = jest.fn();
    render(<SchemaTable schema={schema} overrides={{}} onOverride={onOverride} />);

    const ageSelect = screen.getByRole("combobox", { name: /type for age/i });
    await user.selectOptions(ageSelect, "categorical");

    expect(onOverride).toHaveBeenCalledWith("age", "categorical");
  });

  it("shows overridden badge when type differs from detected", () => {
    render(
      <SchemaTable
        schema={schema}
        overrides={{ age: "boolean" }}
        onOverride={jest.fn()}
      />
    );
    expect(screen.getByText("(overridden)")).toBeInTheDocument();
  });

  it("does not show overridden badge when type matches detected", () => {
    render(
      <SchemaTable
        schema={schema}
        overrides={{ age: "numeric" }}
        onOverride={jest.fn()}
      />
    );
    expect(screen.queryByText("(overridden)")).not.toBeInTheDocument();
  });
});
