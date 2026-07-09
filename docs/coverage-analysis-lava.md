# Reading the coverage database in LAVA: what the reports mean and how to work them

**Short version:** run a batch with `--coverage`, double-click the `batch_apps.lava` file it leaves at your output folder, and LAVA opens a "Batch Coverage" category. Start at **Coverage Summary**, get your target list from **Apps Not Parsed - Rollup**, and sanity-check the verdicts with **Module App Spread**. Everything else is drill-down.

**Long version:** keep reading.

## First, understand what "parsed" means here

Every verdict in this database comes down to one rule:

> An app counts as **parsed** when at least one *app-specific* LEAPP module matched at least one file belonging to that app.

Two words in there do a lot of work:

- **App-specific.** Some modules touch nearly every app on the phone without decoding anything — `appGrouplisting` reads every container's metadata plist (600+ apps per image in my corpus), `userDefaults` sweeps every preference plist. If those counted, every app would look "parsed" and the whole exercise would be useless. So any module whose matches spread across **10 or more apps** in an extraction is classified *generic* and doesn't count. Only the focused modules — the `chatgpt`s and `discordChats`es of the world — earn an app its "parsed" checkmark.
- **Belonging to.** On iOS an app's most interesting data often lives in a *shared group container* (`group.com.kik.chat`) with a different identifier than the app itself. The aggregator folds group and extension containers into the app that owns them, so Kik gets credit when its group container gets parsed. Without that fold, half the messaging apps look unparsed when they aren't.

Also remember what parsed does NOT mean: it does not mean parsed *well*. One matched plist counts. This database tells you where to look, not how good the module is.

## The reports, in the order I read them

**1. Coverage Summary** — the scoreboard. One row per image: device, OS version, apps present, apps parsed, percent covered, and how many files were inventoried. On my five iOS test images the numbers run 1.6% to 3.3% of installed apps parsed by app-specific modules. Before you gasp — most of the 800 apps on any phone are keyboards, stickers, and single-purpose junk nobody will ever write a module for. The percentage isn't the point. The point is what's IN the unparsed list.

**2. Apps Not Parsed - Rollup** — the target list, and the reason this whole thing exists. One row per app across ALL your images: how many images it appears in, how many it went unparsed in, and total files on disk. Sort by files on disk, descending. In my corpus the top of that list was Snapchat (unparsed in 5 of 5 images, 29k files sitting there), Facebook (4 of 4), Twitter, and YouTube. That's a module-writing roadmap, ranked by evidence volume.

**3. Apps Not Parsed - Per Extraction** — the same thing, image by image. This is where the interesting nuances live. Instagram showed up in all five of my images but was unparsed in only one — meaning the existing modules work, but something about that one image (app version? migration? new path?) broke pattern matching. That's a bug report waiting to be filed, and it's a different kind of finding than "no module exists."

**4. App Coverage** — the full ledger: every app, every image, three numbers. `files_on_disk` (what the app left behind), `files_matched` (what ANY module touched), `files_matched_specific` (what app-specific modules touched — the one that matters). Use it to check a specific app: filter by bundle ID or package name and you get its whole story across the corpus.

**5. Module App Spread** — the audit trail for the generic/specific split. One row per module per image with the count of distinct apps it matched. Sort descending and eyeball two zones: the top should be obviously-generic sweeps (it will be), and the zone around 10 is where borderline calls happen. In my data `discordChats` matched 10 apps in one image (classified generic there) and 6 in another (specific there). If you ever see a real decoder land in generic territory, either the threshold needs adjusting for your corpus or that module's search patterns are broader than they should be — both are worth knowing.

**6. Unknown Containers** — the safety net. App containers whose owner could not be identified at all. On a good full filesystem extraction this should be **zero** (every container carries its ownership metadata), and zero is a quality signal. Non-zero means partial extraction, damaged metadata, or leftovers from uninstalls — either way, files that belong to nobody, listed so they don't silently vanish from the analysis.

**7. The raw tables** — `extractions`, `installed_apps`, `app_files`, `artifact_files`. Everything above is built from these. `app_files` is the big one: every file in every image, mapped to its owning app, with sizes and modified times. When a report row makes you curious, this is where you go digging.

## Three analyses to run today

**Find your next module.** Open Apps Not Parsed - Rollup, sort by total files on disk, skip the system apps you don't care about, and take the top entry that appears in multiple images. That's the highest-value gap in the tooling, proven with real data.

**Verify an app you care about.** Working a case with an app you don't trust the tooling on? Check App Coverage for it. If `files_matched_specific` is zero, the LEAPPs decoded nothing from that app and you know to go manual. If it's nonzero, `artifact_files` tells you exactly which modules touched which of its files.

**Compare releases.** This is the regression play: run the batch with the current release into one folder, run it again with the new release into another, and compare the two rollups. Apps dropping off the not-parsed list means coverage went up. Apps APPEARING on it means something broke — maybe an app update moved its database and the module's search patterns need a refresh.

One last note: the `Modified Time` column in `app_files` is stored as text on purpose. Zip archives carry zone-less timestamps, so forcing them into UTC would be lying to you. Tar and directory sources are UTC. Check your extraction type before you lean on those values.

Free tools, real validation, and now a data-driven list of what to build next. That's open source moving the field forward.

Questions? Find me at https://abrignoni.github.io or email abrignoni[at]duck[dot]com.
