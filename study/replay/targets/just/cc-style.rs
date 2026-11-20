#[test]
fn probe_style_bright_foreground() {
  Test::new()
    .justfile(
      "
        foo:
          @echo '{{ style('bright-red') }}foo{{NORMAL}}'
      ",
    )
    .stdout("\x1b[91mfoo\x1b[0m\n")
    .success();
}

#[test]
fn probe_style_bright_foreground_prefixed() {
  Test::new()
    .justfile(
      "
        foo:
          @echo '{{ style('fg:bright-green') }}foo{{NORMAL}}'
      ",
    )
    .stdout("\x1b[92mfoo\x1b[0m\n")
    .success();
}

#[test]
fn probe_style_bright_background() {
  Test::new()
    .justfile(
      "
        foo:
          @echo '{{ style('bg:bright-blue') }}foo{{NORMAL}}'
      ",
    )
    .stdout("\x1b[104mfoo\x1b[0m\n")
    .success();
}

#[test]
fn probe_style_bright_black_and_white() {
  Test::new()
    .justfile(
      "
        foo:
          @echo '{{ style('bright-black') }}foo{{NORMAL}}{{ style('bright-white') }}bar{{NORMAL}}'
      ",
    )
    .stdout("\x1b[90mfoo\x1b[0m\x1b[97mbar\x1b[0m\n")
    .success();
}
