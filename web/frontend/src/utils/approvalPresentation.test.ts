import { describe, expect, it } from "vitest";
import { describeApprovalPurpose } from "./approvalPresentation";

describe("describeApprovalPurpose", () => {
  it("explains a read-only Select-String request in product language", () => {
    const purpose = describeApprovalPurpose("run_command", {
      command: 'Select-String -Path "app/web/static/styles.css" -Pattern "priority|conflict"',
    });

    expect(purpose).toContain("app/web/static/styles.css");
    expect(purpose).toContain("只读检查");
  });

  it("explains that file edits are immediate and are not rollback", () => {
    const purpose = describeApprovalPurpose("edit_file", {
      file_path: "app/services/campaign_service.py",
    });

    expect(purpose).toContain("campaign_service.py");
    expect(purpose).toContain("立即写入");
    expect(purpose).toContain("不会自动回滚");
  });
});
