# Builds the Playwright engine (the one the README recommends) into a
# container with its own Chromium -- for a CI canary run or a scheduled job,
# not required for local development (`pip install` directly is simpler
# there).
#
#   docker build -t indiegogo-scraper .
#   docker run --rm -v "$PWD/out:/out" indiegogo-scraper \
#     --pages 3 --out /out/indiegogo_projects
#
# Pass --proxy/--twocaptcha-key the same way as running locally, or mount a
# .env at /app/.env -- nothing here bakes in a credential. A .env baked into
# an image is a credential published to everyone who can pull it, which is
# why .dockerignore excludes it and CI asserts the built image has none.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-playwright.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-playwright.txt \
    # Playwright's own apt-get for Chromium's shared-library dependencies --
    # not pip packages, so this has to run as a separate, explicit step.
    && playwright install --with-deps chromium

# Every module playwright_scraper.py imports, transitively, plus diff_runs.py
# as a useful companion in the same image. smoke_test.py's
# test_dockerfile_copies_import_graph checks this list against the
# entrypoint's real import graph -- this family has shipped a broken image
# from a missing COPY line three times before, because nothing else in a
# repo like this ever builds the image.
COPY captcha_solver.py env_config.py fingerprint_client.py output_writer.py \
     page_flow.py playwright_scraper.py product_parser.py proxy_pool.py \
     scraper_api_client.py diff_runs.py ./

ENTRYPOINT ["python3", "playwright_scraper.py"]
CMD ["--help"]
