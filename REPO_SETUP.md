# Publishing this starter project to GitHub

GitHub's normal website can create the remote repository. This archive is intentionally formatted as a full repository working tree (including `.github/workflows`).

## Browser/Chromebook path

1. Sign in to GitHub and create an **empty public repository** named `clipforge-local-studio` on your account. No README/license/gitignore is necessary because this archive includes them.
2. Extract the source ZIP locally. On GitHub, select **Add file → Upload files** and drag the files/folders from the extracted directory into the new repo (not the ZIP itself). Ensure the `.github/workflows/build-windows.yml` nested directory is included. If the browser skips hidden folders, upload the workflow separately using GitHub's create-file UI at that exact path.
3. Commit to the default `main` branch. Go to **Actions → Build portable Windows EXE**, then run the workflow, or a push to `main` will build automatically.
4. Download the resulting ZIP from the successful run's Artifacts.

## Git CLI path (locally, if git is available)

```bash
git init
git branch -M main
git add .
git commit -m 'Initial ClipForge Studio local AI video editor'
git remote add origin https://github.com/YOUR_USERNAME/clipforge-local-studio.git
git push -u origin main
```

The GitHub Actions Windows runner, not your Chromebook, will package `ClipForgeStudio.exe`. For a persistent downloadable Release, tag a commit like `v0.1.0` and push the tag (workflow has release automation).
