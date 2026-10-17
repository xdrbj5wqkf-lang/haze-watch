# Haze Watch

A 72-hour haze outlook for our places, built for iPhone and iPad. Every hour a free GitHub job pulls fire hotspots (NASA FIRMS), the wind, rain and PM2.5 forecast (Open-Meteo / CAMS) and live station readings (WAQI). It tracks smoke from each fire cluster along the wind and publishes the result as a web app you add to your Home Screen.

## Set it up (about 15 minutes, no terminal needed)

1. **Create the repository.** Sign in at github.com, choose **New repository**, name it `haze-watch` and set it to **Public**. GitHub Pages is free only for public repositories. Leave every "add a file" box unticked.
2. **Upload the files.** Unzip `haze-watch.zip` on your Mac. In Finder press **Cmd + Shift + .** so the hidden `.github` folder shows. On the new repository page click **uploading an existing file**, drag in everything inside the unzipped folder (including `.github`), then click **Commit changes**.
3. **Add your keys.** Go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `WAQI_TOKEN`: your WAQI token (live station readings and the forecast check)
   - Optional, and off by default: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` switch on a morning digest and early warnings by Telegram.

   The FIRMS key is not needed: the job uses NASA's keyless regional files.
4. **Switch on the web app.** **Settings → Pages → Build and deployment → Source: GitHub Actions.**
5. **Run it once.** Open the **Actions** tab, enable workflows if asked, choose **Update Haze Watch → Run workflow**. It takes 2–4 minutes. After that it runs every hour by itself.
6. **Install on iPhone and iPad.** Open `https://<your-github-username>.github.io/haze-watch/` in **Safari**, tap **Share → Add to Home Screen**. It opens full screen like an app. Repeat on each device.
7. **Link the digest to the app.** Edit `config.json` on GitHub (the pencil icon) and put that address in `"app_url"`.

## Change your places

Edit `config.json` on GitHub and change the `places` list (name, latitude, longitude). The app recomputes on commit. Up to about ten places is fine.

**Privacy:** the repository and the web app are public, so anyone with the address can see the places you list. Use a suburb or town, not your exact home.

## What runs when

| When | What |
| --- | --- |
| Every hour at about :07 | Fetch fires, forecast and stations; run the smoke model; publish the app |
| 6am Malaysia time | Telegram digest, only if the Telegram secrets are set |

The `data/history.csv` file keeps 90 days of readings and forecasts. The app's **How good are our guesses?** card compares three 24-hour-ahead guesses with what the stations then read: our best estimate, the CAMS forecast alone, and simply assuming tomorrow looks like today.

## Things to know

- PM2.5 is converted to the Malaysian API with DOE's own breakpoints (12, 75.5, 150.4, 250.4, 350.4 and 500.4 µg/m³ for API 50 to 500; DOE "API Calculation" guidance). DOE applies them to a 24-hour average; the app applies them to the latest hourly reading, so it reacts faster than APIMS. The app also shows the 24-hour average once a day of readings has built up.
- WAQI publishes US AQI rather than concentrations; the job converts back using the US EPA pre-2024 table, which WAQI has historically used. Check this too.
- The smoke tracking is uncalibrated. Read it for direction and timing until the forecast check has a few weeks of data.
- GitHub pauses scheduled jobs on repositories with no activity for 60 days. The hourly history commits should count as activity; if it ever stops, press **Run workflow** once.
- Indicative only. For official readings use APIMS: https://eqms.doe.gov.my/APIMS/main

## Files

- `pipeline/run.py`: the hourly job (Python standard library only)
- `site/`: the web app (`index.html`, offline support in `sw.js`, icons, coastline map)
- `config.json`: places, alert settings
- `.github/workflows/update.yml`: the hourly schedule
