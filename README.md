# Haze Watch

A 72-hour haze outlook for your iPhone and iPad. Every hour a free GitHub job pulls fire hotspots (NASA FIRMS), the wind, rain and PM2.5 forecast (Open-Meteo / CAMS) and live station readings (WAQI). It tracks smoke from each fire cluster along the wind and publishes the result as a web app you add to your Home Screen. A Telegram bot sends a morning digest and a heads-up before a place turns unhealthy.

## Set it up (about 15 minutes, no terminal needed)

1. **Create the repository.** Sign in at github.com, choose **New repository**, name it `haze-watch` and set it to **Public**. GitHub Pages is free only for public repositories. Leave every "add a file" box unticked.
2. **Upload the files.** Unzip `haze-watch.zip` on your Mac. In Finder press **Cmd + Shift + .** so the hidden `.github` folder shows. On the new repository page click **uploading an existing file**, drag in everything inside the unzipped folder (including `.github`), then click **Commit changes**.
3. **Add your keys.** Go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `WAQI_TOKEN`: your WAQI token (live station readings and the forecast check)
   - `TELEGRAM_BOT_TOKEN`: the token BotFather gave you
   - `TELEGRAM_CHAT_ID`: your numeric Telegram ID. Send your bot any message first, then message **@userinfobot**, which replies with your ID.

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
| 6am Malaysia time | Telegram digest for every place |
| Any hour outside 10pm–6am | Heads-up if a place is forecast to cross API 100 within 24 hours (at most once per 12 hours per place) |

The `data/history.csv` file keeps 90 days of readings and forecasts. The app's **Forecast check** compares the 24-hour-ahead CAMS forecast with station readings and with simply assuming tomorrow looks like today.

## Things to know

- The PM2.5-to-Malaysian-API conversion uses approximate breakpoints (15, 35, 150, 250 µg/m³ for API 50, 100, 200, 300). Confirm them against DOE guidance before relying on the bands.
- WAQI publishes US AQI rather than concentrations; the job converts back using the US EPA pre-2024 table, which WAQI has historically used. Check this too.
- The smoke tracking is uncalibrated. Read it for direction and timing until the forecast check has a few weeks of data.
- GitHub pauses scheduled jobs on repositories with no activity for 60 days. The hourly history commits should count as activity; if it ever stops, press **Run workflow** once.
- Indicative only. For official readings use APIMS: https://eqms.doe.gov.my/APIMS/main

## Files

- `pipeline/run.py`: the hourly job (Python standard library only)
- `site/`: the web app (`index.html`, offline support in `sw.js`, icons, coastline map)
- `config.json`: places, alert settings
- `.github/workflows/update.yml`: the hourly schedule
