# Force release builder (SPEC §46): static (musl) binaries that run on any Linux of the same architecture.
# Builds Force's own tmux (pinned, checksummed sources) into /opt/force-tmux, and carries a Rust toolchain for
# the sidebar, header, dialog and intro. scripts/build-release.sh runs it; nothing here touches the host.
FROM alpine:3.20

RUN apk add --no-cache build-base curl bison pkgconf linux-headers perl bash file

ARG TMUX_VERSION=3.4
ARG TMUX_SHA256=551ab8dea0bf505c0ad6b7bb35ef567cdde0ccb84357df142c254f35a23e19aa
ARG LIBEVENT_VERSION=2.1.12-stable
ARG LIBEVENT_SHA256=92e6de1be9ec176428fd2367677e61ceffc2ee1cb119035037a27d346b0403bb
ARG NCURSES_VERSION=6.5
ARG NCURSES_SHA256=136d91bc269a9a5785e5f9e980bc76ab57428f604ce3e5a5a90cebc767971cc6

WORKDIR /tmp/b
COPY src/ /tmp/b/
# sources come from build/src (downloaded and checked by build-release.sh); check them again here
RUN echo "$TMUX_SHA256  tmux-$TMUX_VERSION.tar.gz" | sha256sum -c - \
 && echo "$LIBEVENT_SHA256  libevent-$LIBEVENT_VERSION.tar.gz" | sha256sum -c - \
 && echo "$NCURSES_SHA256  ncurses-$NCURSES_VERSION.tar.gz" | sha256sum -c -

# ncurses: static, and it looks for terminfo where every common distro keeps it (Debian/Ubuntu: /lib/terminfo
# and /usr/share/terminfo, Fedora/Arch: /usr/share/terminfo, Alpine: /etc/terminfo), plus Force's own
# (TERMINFO_DIRS set by Force wins).
RUN tar xzf ncurses-$NCURSES_VERSION.tar.gz && cd ncurses-$NCURSES_VERSION \
 && ./configure --prefix=/opt/deps --without-shared --without-debug --without-ada --without-cxx-binding \
      --without-manpages --without-tests --enable-widec --enable-pc-files \
      --with-pkg-config-libdir=/opt/deps/lib/pkgconfig \
      --with-terminfo-dirs=/etc/terminfo:/lib/terminfo:/usr/share/terminfo:/usr/lib/terminfo \
      --with-default-terminfo-dir=/usr/share/terminfo \
 && make -j"$(nproc)" && make install

RUN tar xzf libevent-$LIBEVENT_VERSION.tar.gz && cd libevent-$LIBEVENT_VERSION \
 && ./configure --prefix=/opt/deps --disable-shared --enable-static --disable-openssl --disable-samples \
      --disable-libevent-regress \
 && make -j"$(nproc)" && make install

RUN tar xzf tmux-$TMUX_VERSION.tar.gz && cd tmux-$TMUX_VERSION \
 && PKG_CONFIG_PATH=/opt/deps/lib/pkgconfig ./configure --prefix=/opt/force-tmux --enable-static \
      CFLAGS="-I/opt/deps/include -I/opt/deps/include/ncursesw -O2" LDFLAGS="-L/opt/deps/lib -static" \
 && make -j"$(nproc)" && make install && strip /opt/force-tmux/bin/tmux \
 && /opt/force-tmux/bin/tmux -V

# Rust (static musl target is the host target on Alpine)
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo PATH=/opt/cargo/bin:$PATH
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable --no-modify-path \
 && rustc -vV
