# Two executable images from one build, one per stage.
#
#   docker build --target fetch-worker -t hearthai/webfetch-fetch .
#   docker build --target inspector    -t hearthai/webfetch-inspect .
#
# They are built together because they share a workspace, and shipped apart
# because they have different permissions: only the fetch stage gets network
# egress, and only the inspection stage ever produces a result.

FROM rust:1.94.1-bookworm AS build
WORKDIR /src
# Manifests first, so a source-only change does not refetch the index.
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY contracts/Cargo.toml contracts/Cargo.toml
COPY fetch-worker/Cargo.toml fetch-worker/Cargo.toml
COPY inspector/Cargo.toml inspector/Cargo.toml
RUN mkdir -p contracts/src fetch-worker/src inspector/src \
 && echo '' > contracts/src/lib.rs \
 && echo 'fn main() {}' > fetch-worker/src/main.rs \
 && echo 'fn main() {}' > inspector/src/main.rs \
 && cargo build --release --locked \
 && rm -rf contracts/src fetch-worker/src inspector/src
COPY . .
# COPY keeps the context's timestamps, which can be older than the artifacts the
# dummy build just produced; touching every source makes cargo rebuild the real
# code rather than ship the placeholder binaries.
# --locked: the image is built from the committed dependency graph or not at all.
RUN find . -name '*.rs' -exec touch {} + && cargo build --release --locked

# Distroless, non-root, no shell. The fetch stage needs no CA bundle from the
# distribution: its TLS roots are compiled in.
FROM gcr.io/distroless/cc-debian12:nonroot AS fetch-worker
COPY --from=build /src/target/release/webfetch-fetch /usr/local/bin/webfetch-fetch
USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/webfetch-fetch"]

FROM gcr.io/distroless/cc-debian12:nonroot AS inspector
COPY --from=build /src/target/release/webfetch-inspect /usr/local/bin/webfetch-inspect
# A default bundle ships in the image; an operator-managed bundle is mounted
# read-only over it and selected with --rules.
COPY --from=build /src/rules /opt/webfetch/rules
USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/webfetch-inspect"]
