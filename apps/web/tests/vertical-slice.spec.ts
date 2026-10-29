import { expect, test } from "@playwright/test";
import path from "node:path";

// End-to-end vertical slice through the browser. Requires:
//   LK_E2E_USERNAME / LK_E2E_PASSWORD  - the bootstrap account
//   a running worker (it processes the upload) with OCR available, or accept the OCR warning path.
const user = process.env.LK_E2E_USERNAME ?? "keeper";
const password = process.env.LK_E2E_PASSWORD ?? "";
const fixture = path.resolve(__dirname, "../../../fixtures/synthetic_adventures/lantern_under_saltmere.pdf");
const stamp = Date.now();

test("upload, review, create character, play, resume", async ({ page }) => {
  test.skip(!password, "set LK_E2E_PASSWORD to run");

  await page.goto("/login");
  await page.getByLabel("Username").fill(user);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Campaigns" })).toBeVisible();

  // Library: create an adventure and upload the synthetic PDF.
  await page.getByRole("link", { name: "Adventure library" }).click();
  await page.getByLabel("Title").fill(`Saltmere ${stamp}`);
  await page.getByRole("button", { name: "Create adventure" }).click();
  const section = page.getByRole("region", { name: `Saltmere ${stamp}` });
  await section.getByText("Upload a PDF").click();
  await section.getByLabel("PDF (adventure module or zine)").setInputFiles(fixture);
  await section.getByLabel("Your rights basis").selectOption("ORIGINAL_WORK");
  await section.getByRole("checkbox").check();
  await section.getByRole("button", { name: "Upload and process" }).click();
  await section.getByRole("link", { name: "saltmere.pdf" }).or(section.getByRole("link", { name: /lantern_under_saltmere/ })).click();
  await expect(page.getByText("READY_FOR_REVIEW")).toBeVisible({ timeout: 60_000 });

  // Review: approve the gate, the chapel, Wren, and the connection between the two locations.
  await page.getByRole("link", { name: "Review extracted candidates" }).click();
  for (const name of ["LOCATION The Lantern Gate", "LOCATION The Drowned Chapel", "NPC Wren Halloway",
    "CONNECTION The Lantern Gate -> The Drowned Chapel", "EDGE The Lantern Gate CONTAINS Wren Halloway"]) {
    const card = page.getByRole("article", { name });
    await card.getByRole("button", { name: "Approve" }).click();
    await expect(card).toBeHidden();
  }

  // Campaign and character.
  await page.getByRole("link", { name: "Campaigns" }).first().click();
  await page.getByLabel("Name").fill(`Run ${stamp}`);
  await page.getByLabel("Adventure").selectOption({ label: `Saltmere ${stamp}` });
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Create campaign" }).click();
  await page.getByRole("article", { name: `Run ${stamp}` }).getByRole("link", { name: "Set up" }).click();
  await page.getByLabel("Name").fill("Ash");
  await page.getByLabel("WIS").fill("15");
  await page.getByLabel(/^Torch/).fill("2");
  await page.getByRole("button", { name: "Create character" }).click();
  await page.getByRole("button", { name: "Start in the first approved location" }).click();

  // Play: ask about the location, then move.
  await expect(page.getByRole("heading", { name: "The Lantern Gate" })).toBeVisible();
  await page.getByLabel("Your action").fill("What does this place look like?");
  await page.getByRole("button", { name: "Act" }).click();
  await expect(page.getByText(/squat stone gatehouse/).first()).toBeVisible();
  await page.getByLabel("Your action").fill("I walk north to the drowned chapel");
  await page.getByRole("button", { name: "Act" }).click();
  await expect(page.getByRole("heading", { name: "The Drowned Chapel" })).toBeVisible();

  // Resume: a reload shows the same committed state.
  await page.reload();
  await expect(page.getByRole("heading", { name: "The Drowned Chapel" })).toBeVisible();
});
