(() => {
  "use strict";

  const MAX_FILE_BYTES = 250 * 1024 * 1024;
  const accessForm = document.querySelector("#access-form");
  const accessCode = document.querySelector("#access-code");
  const accessButton = document.querySelector("#access-button");
  const accessStatus = document.querySelector("#access-status");
  const uploadForm = document.querySelector("#project-upload-form");
  const projectName = document.querySelector("#project-name");
  const fileInput = document.querySelector("#project-files");
  const selectedFiles = document.querySelector("#selected-files");
  const uploadButton = document.querySelector("#upload-button");
  const uploadStatus = document.querySelector("#upload-status");
  const uploadResults = document.querySelector("#upload-results");
  let accessToken = "";

  function setStatus(element, message, state = "") {
    element.textContent = message;
    if (state) element.dataset.state = state;
    else delete element.dataset.state;
  }

  async function api(path, payload) {
    const result = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    let body = {};
    try {
      body = await result.json();
    } catch (_) {
      body = {};
    }
    if (!result.ok) throw new Error(body.message || "The request could not be completed.");
    return body;
  }

  function readableSize(bytes) {
    const mib = bytes / (1024 * 1024);
    return mib >= 1 ? `${mib.toFixed(mib >= 10 ? 1 : 2)} MiB` : `${Math.ceil(bytes / 1024)} KiB`;
  }

  function chosenFiles() {
    return Array.from(fileInput.files || []);
  }

  function showSelection() {
    const files = chosenFiles();
    if (!files.length) {
      selectedFiles.textContent = "No files selected.";
      return;
    }
    const tooLarge = files.filter((file) => file.size > MAX_FILE_BYTES);
    selectedFiles.innerHTML = `<strong>${files.length} file${files.length === 1 ? "" : "s"} selected</strong> · ${readableSize(files.reduce((sum, file) => sum + file.size, 0))} total`;
    if (tooLarge.length) {
      selectedFiles.append(document.createTextNode(` · ${tooLarge.length} exceed the 250 MiB per-file limit`));
    }
  }

  function progressRow(file) {
    const row = document.createElement("li");
    row.className = "upload-result";
    row.dataset.state = "waiting";
    const name = document.createElement("span");
    name.className = "upload-result-name";
    name.textContent = `${file.name} (${readableSize(file.size)})`;
    const state = document.createElement("span");
    state.className = "upload-result-state";
    state.textContent = "Waiting";
    const progress = document.createElement("progress");
    progress.max = 100;
    progress.value = 0;
    row.append(name, state, progress);
    uploadResults.append(row);
    return { row, state, progress };
  }

  function sendToS3(file, grant, display) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      const form = new FormData();
      Object.entries(grant.fields).forEach(([key, value]) => form.append(key, value));
      form.append("file", file);
      request.open("POST", grant.url);
      request.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        display.progress.value = Math.round((event.loaded / event.total) * 100);
        display.state.textContent = `${display.progress.value}%`;
        display.row.dataset.state = "uploading";
      });
      request.addEventListener("load", () => {
        if (request.status >= 200 && request.status < 300) {
          display.progress.value = 100;
          display.state.textContent = "Complete";
          display.row.dataset.state = "complete";
          resolve();
        } else {
          reject(new Error(`Storage rejected ${file.name}.`));
        }
      });
      request.addEventListener("error", () => reject(new Error(`The upload of ${file.name} was interrupted.`)));
      request.addEventListener("abort", () => reject(new Error(`The upload of ${file.name} was cancelled.`)));
      request.send(form);
    });
  }

  accessForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    accessButton.disabled = true;
    setStatus(accessStatus, "Checking access…");
    try {
      const result = await api("/api/project-upload/access", { code: accessCode.value });
      accessToken = result.accessToken;
      accessCode.value = "";
      accessForm.hidden = true;
      uploadForm.hidden = false;
      setStatus(accessStatus, "");
      projectName.focus();
    } catch (error) {
      setStatus(accessStatus, error.message, "error");
    } finally {
      accessButton.disabled = false;
    }
  });

  fileInput.addEventListener("change", showSelection);

  uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const files = chosenFiles();
    const invalid = files.find((file) => file.size < 1 || file.size > MAX_FILE_BYTES);
    if (!files.length) {
      setStatus(uploadStatus, "Choose at least one file.", "error");
      return;
    }
    if (invalid) {
      setStatus(uploadStatus, `${invalid.name} must be 250 MiB or smaller and cannot be empty.`, "error");
      return;
    }

    uploadButton.disabled = true;
    fileInput.disabled = true;
    projectName.readOnly = true;
    uploadResults.replaceChildren();
    const displays = files.map(progressRow);
    let completed = 0;
    try {
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        const display = displays[index];
        display.state.textContent = "Preparing";
        const result = await api("/api/project-upload/grants", {
          accessToken,
          projectName: projectName.value,
          files: [{ name: file.name, size: file.size, type: file.type }],
        });
        await sendToS3(file, result.files[0].upload, display);
        completed += 1;
        setStatus(uploadStatus, `Uploaded ${completed} of ${files.length} files…`);
      }
      setStatus(uploadStatus, `${completed} file${completed === 1 ? "" : "s"} added to this project.`, "success");
      fileInput.value = "";
      showSelection();
    } catch (error) {
      const display = displays[completed];
      if (display) {
        display.state.textContent = "Failed";
        display.row.dataset.state = "error";
      }
      setStatus(uploadStatus, `${error.message} Files already marked complete were saved; select only the remaining files before retrying.`, "error");
      if (/access has expired/i.test(error.message)) {
        accessToken = "";
        uploadForm.hidden = true;
        accessForm.hidden = false;
        accessCode.focus();
      }
    } finally {
      uploadButton.disabled = false;
      fileInput.disabled = false;
      projectName.readOnly = false;
    }
  });

  showSelection();
})();
