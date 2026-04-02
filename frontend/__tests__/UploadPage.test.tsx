import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UploadPage from "@/app/page";
import * as api from "@/lib/api";

const mockPush = jest.fn();

// Mock next/navigation
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

// Mock API module
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  uploadCSV: jest.fn(),
  generateJob: jest.fn(),
}));

const mockUpload = api.uploadCSV as jest.MockedFunction<typeof api.uploadCSV>;
const mockGenerate = api.generateJob as jest.MockedFunction<typeof api.generateJob>;

function makeCSV(name = "data.csv", size = 100): File {
  return new File([new Uint8Array(size)], name, { type: "text/csv" });
}

describe("UploadPage", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("renders the upload zone initially", () => {
    render(<UploadPage />);
    expect(screen.getByRole("button", { name: /csv upload zone/i })).toBeInTheDocument();
  });

  it("shows schema table after successful upload", async () => {
    mockUpload.mockResolvedValueOnce({
      dataset_id: "ds-1",
      row_count: 500,
      schema: [
        { name: "age", detected_type: "numeric" },
        { name: "city", detected_type: "categorical" },
      ],
    });

    const user = userEvent.setup();
    render(<UploadPage />);

    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    await user.upload(input, makeCSV());

    await waitFor(() => {
      expect(screen.getByText("Inferred Schema")).toBeInTheDocument();
    });
    expect(screen.getByText("age")).toBeInTheDocument();
    expect(screen.getByText("city")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /generate/i })).toBeInTheDocument();
  });

  it("shows network error when upload fails", async () => {
    mockUpload.mockRejectedValueOnce(new Error("Network error"));

    const user = userEvent.setup();
    render(<UploadPage />);

    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    await user.upload(input, makeCSV());

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/network error/i);
    });
  });

  it("shows 100k row error when server rejects oversized file", async () => {
    mockUpload.mockRejectedValueOnce(
      new api.ApiError(422, "File exceeds 100k row limit")
    );

    const user = userEvent.setup();
    render(<UploadPage />);

    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    await user.upload(input, makeCSV());

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/100k row limit/i);
    });
  });

  it("calls generateJob and navigates on Generate click", async () => {
    mockPush.mockClear();
    mockUpload.mockResolvedValueOnce({
      dataset_id: "ds-1",
      row_count: 100,
      schema: [{ name: "x", detected_type: "numeric" }],
    });
    mockGenerate.mockResolvedValueOnce({ job_id: "job-abc" });

    const user = userEvent.setup();
    render(<UploadPage />);

    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    await user.upload(input, makeCSV());

    await waitFor(() => screen.getByRole("button", { name: /generate/i }));
    await user.click(screen.getByRole("button", { name: /generate/i }));

    await waitFor(() => {
      expect(mockGenerate).toHaveBeenCalledWith(
        expect.objectContaining({ dataset_id: "ds-1" })
      );
    });
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/jobs/job-abc");
    });
  });
});
