#[cfg(target_os = "macos")]
#[test]
fn probe_illumos_recipe_is_disabled_elsewhere() {
  Test::new()
    .justfile(
      "
        [illumos]
        foo:
          echo bar

        [macos]
        foo:
          echo baz
      ",
    )
    .stdout("baz\n")
    .stderr("echo baz\n")
    .success();
}

#[cfg(target_os = "macos")]
#[test]
fn probe_solaris_recipe_is_disabled_elsewhere() {
  Test::new()
    .justfile(
      "
        [solaris]
        foo:
          echo bar

        [macos]
        foo:
          echo baz
      ",
    )
    .stdout("baz\n")
    .stderr("echo baz\n")
    .success();
}

#[cfg(target_os = "macos")]
#[test]
fn probe_solaris_and_illumos_combine_with_other_systems() {
  Test::new()
    .justfile(
      "
        [solaris]
        [illumos]
        [macos]
        foo:
          echo bar
      ",
    )
    .stdout("bar\n")
    .stderr("echo bar\n")
    .success();
}
