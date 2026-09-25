const form = document.querySelector("#inquiry-form");
const fileInput = document.querySelector("#file-input");
const fileSummary = document.querySelector("#file-summary");
const formStatus = document.querySelector("#form-status");
const submitButton = form.querySelector("button[type='submit']");

document.querySelector("#year").textContent = String(new Date().getFullYear());

const fullscreenMedia = document.querySelectorAll(
  ".work-card-media--duo picture, .work-card > .work-card-media:not(.work-card-media--duo), .pen-item picture, .woodwork-item picture, .gallery-media"
);

// A modal keeps the image usable when a mobile browser cannot fullscreen HTML.
// Fullscreen the inner div: the Fullscreen API does not accept dialog elements.
const imageDialog = document.createElement("dialog");
imageDialog.className = "image-fullscreen-dialog";
imageDialog.setAttribute("aria-label", "Fullscreen image");
imageDialog.innerHTML = '<div class="image-fullscreen-view"><img alt=""><button type="button" class="fullscreen-toggle" autofocus>Exit fullscreen</button></div>';
document.body.append(imageDialog);
const imageView = imageDialog.querySelector(".image-fullscreen-view");
const fullscreenImage = imageView.querySelector("img");
let imageTrigger = null;
let imageWasNativeFullscreen = false;

async function closeFullscreenImage() {
  if (activeFullscreenElement() === imageView) {
    const exitFullscreen = document.exitFullscreen || document.webkitExitFullscreen;
    try {
      await exitFullscreen.call(document);
    } catch (error) {
      console.warn("Fullscreen could not be closed.", error);
      return;
    }
  }
  if (imageDialog.open) imageDialog.close();
}

imageView.querySelector("button").addEventListener("click", closeFullscreenImage);
imageDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeFullscreenImage();
});
imageDialog.addEventListener("close", () => {
  document.documentElement.classList.remove("image-fullscreen-open");
  imageWasNativeFullscreen = false;
  imageTrigger?.setAttribute("aria-pressed", "false");
  imageTrigger?.focus({ preventScroll: true });
  imageTrigger = null;
  fullscreenImage.removeAttribute("src");
});

async function openFullscreenImage(container) {
  const image = container.querySelector("img");
  imageTrigger = container.querySelector(".fullscreen-toggle");
  fullscreenImage.src = image.currentSrc || image.src;
  fullscreenImage.alt = image.alt;
  imageTrigger.setAttribute("aria-pressed", "true");
  document.documentElement.classList.add("image-fullscreen-open");
  imageDialog.showModal();
  const requestFullscreen = imageView.requestFullscreen || imageView.webkitRequestFullscreen;
  if (requestFullscreen) {
    try {
      await requestFullscreen.call(imageView);
    } catch {
      // Keep the viewport-filling modal if fullscreen is unsupported or denied.
    }
  }
}

function syncFullscreenImage() {
  if (activeFullscreenElement() === imageView) {
    imageWasNativeFullscreen = true;
  } else if (imageWasNativeFullscreen) {
    imageDialog.close();
  }
}
document.addEventListener("fullscreenchange", syncFullscreenImage);
document.addEventListener("webkitfullscreenchange", syncFullscreenImage);

function activeFullscreenElement() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function updateFullscreenButtons() {
  const active = activeFullscreenElement();
  fullscreenMedia.forEach((container) => {
    const button = container.querySelector(":scope > .fullscreen-toggle");
    if (!button) return;
    const isActive = active === container || (imageDialog.open && imageTrigger === button);
    button.textContent = isActive ? "Exit fullscreen" : "Fullscreen";
    button.setAttribute("aria-label", isActive ? "Exit fullscreen" : "View media fullscreen");
    button.setAttribute("aria-pressed", String(isActive));
  });
}

async function toggleFullscreen(container) {
  if (!container.querySelector("video") && container.querySelector("img")) {
    await openFullscreenImage(container);
    return;
  }
  const active = activeFullscreenElement();
  try {
    if (active === container) {
      const exitFullscreen = document.exitFullscreen || document.webkitExitFullscreen;
      if (exitFullscreen) await exitFullscreen.call(document);
      return;
    }

    const video = container.querySelector("video");
    if (video) {
      loadGalleryVideo(video);
      video.play().catch(() => {});
    }

    const requestFullscreen = container.requestFullscreen || container.webkitRequestFullscreen;
    if (requestFullscreen) {
      await requestFullscreen.call(container);
    } else if (video?.webkitEnterFullscreen) {
      video.webkitEnterFullscreen();
    }
  } catch (error) {
    console.warn("Fullscreen could not be opened.", error);
  }
}

fullscreenMedia.forEach((container) => {
  container.classList.add("fullscreen-media");
  const button = document.createElement("button");
  button.className = "fullscreen-toggle";
  button.type = "button";
  button.textContent = "Fullscreen";
  button.setAttribute("aria-label", "View media fullscreen");
  button.setAttribute("aria-pressed", "false");
  button.addEventListener("click", () => toggleFullscreen(container));
  container.append(button);
});

document.addEventListener("fullscreenchange", updateFullscreenButtons);
document.addEventListener("webkitfullscreenchange", updateFullscreenButtons);

const galleryVideos = document.querySelectorAll(".gallery-media video, .work-card-media > video");
galleryVideos.forEach((video) => {
  const poster = document.createElement("img");
  poster.className = "video-poster";
  poster.src = video.poster;
  poster.alt = "";
  poster.setAttribute("aria-hidden", "true");
  video.parentElement.append(poster);
  video.addEventListener("playing", () => { poster.hidden = true; });
  video.addEventListener("emptied", () => { poster.hidden = false; });
});

function loadGalleryVideo(video) {
  const source = video.querySelector("source");
  if (!source.src) {
    source.src = source.dataset.src;
    video.load();
  }
}

if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  galleryVideos.forEach((video) => video.pause());
} else {
  const videoObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        galleryVideos.forEach((video) => {
          if (video !== entry.target) video.pause();
        });
        loadGalleryVideo(entry.target);
        entry.target.play().catch(() => {});
      } else {
        entry.target.pause();
      }
    });
  }, { rootMargin: "0px", threshold: 0.55 });
  galleryVideos.forEach((video) => videoObserver.observe(video));
}

fileInput.addEventListener("change", () => {
  const files = [...fileInput.files];
  fileSummary.textContent = files.length
    ? files.map((file) => file.name).join(", ")
    : "Images, PDFs, vector files, LightBurn projects, or ZIP archives";
});

function setStatus(message, state = "") {
  formStatus.textContent = message;
  formStatus.dataset.state = state;
}

async function jsonRequest(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.message || "The request could not be completed.");
  return payload;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("");
  if (!form.reportValidity()) return;

  const data = new FormData(form);
  if (data.get("website")) return;

  const files = [...fileInput.files];
  if (files.length > 5) return setStatus("Please choose no more than five files.", "error");
  if (files.some((file) => file.size > 10 * 1024 * 1024)) return setStatus("Each file must be 10 MB or smaller.", "error");

  submitButton.disabled = true;
  try {
    setStatus("Preparing your private upload…");
    const inquiry = {
      name: data.get("name").trim(),
      email: data.get("email").trim(),
      phone: data.get("phone").trim(),
      projectType: data.get("projectType"),
      message: data.get("message").trim(),
      files: files.map((file) => ({ name: file.name, type: file.type || "application/octet-stream", size: file.size }))
    };

    const prepared = await jsonRequest("/api/inquiries", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(inquiry)
    });

    for (let index = 0; index < files.length; index += 1) {
      setStatus(`Uploading file ${index + 1} of ${files.length}…`);
      const upload = prepared.uploads[index];
      const response = await fetch(upload.url, {
        method: "PUT",
        headers: { "content-type": upload.contentType },
        body: files[index]
      });
      if (!response.ok) throw new Error(`Could not upload ${files[index].name}.`);
    }

    setStatus("Sending your project request…");
    await jsonRequest(`/api/inquiries/${encodeURIComponent(prepared.inquiryId)}/submit`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: prepared.token })
    });

    form.reset();
    fileSummary.textContent = "Images, PDFs, vector files, LightBurn projects, or ZIP archives";
    setStatus("Your request has been sent. I’ll review it and get back to you by email.", "success");
  } catch (error) {
    setStatus(error.message || "Something went wrong. Please try again.", "error");
  } finally {
    submitButton.disabled = false;
  }
});
