import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DropZone from "@/components/DropZone";

function makeFile(name: string, size: number, type = "text/csv"): File {
  const content = new Uint8Array(size);
  return new File([content], name, { type });
}

describe("DropZone", () => {
  it("renders the upload zone", () => {
    render(<DropZone onFile={jest.fn()} />);
    expect(screen.getByRole("button", { name: /csv upload zone/i })).toBeInTheDocument();
  });

  it("calls onFile when a valid CSV is selected via input", async () => {
    const user = userEvent.setup();
    const onFile = jest.fn();
    render(<DropZone onFile={onFile} />);

    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    const file = makeFile("data.csv", 100);
    await user.upload(input, file);

    expect(onFile).toHaveBeenCalledWith(file);
  });

  it("shows error and does not call onFile when file exceeds 50MB", async () => {
    const user = userEvent.setup();
    const onFile = jest.fn();
    render(<DropZone onFile={onFile} />);

    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    const bigFile = makeFile("huge.csv", 51 * 1024 * 1024);
    await user.upload(input, bigFile);

    expect(onFile).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/50MB/i);
  });

  it("shows error for non-CSV file", () => {
    const onFile = jest.fn();
    render(<DropZone onFile={onFile} />);

    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    const txtFile = makeFile("data.txt", 100, "text/plain");
    // Use fireEvent.change to bypass the accept attribute filtering in jsdom
    Object.defineProperty(input, "files", { value: [txtFile], configurable: true });
    fireEvent.change(input);

    expect(onFile).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/csv/i);
  });

  it("calls onFile on drop of valid CSV", () => {
    const onFile = jest.fn();
    render(<DropZone onFile={onFile} />);

    const zone = screen.getByRole("button", { name: /csv upload zone/i });
    const file = makeFile("data.csv", 100);
    fireEvent.drop(zone, { dataTransfer: { files: [file] } });

    expect(onFile).toHaveBeenCalledWith(file);
  });
});
