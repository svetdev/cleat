fn probe_unknown_submodule(args: &[&str]) {
  Test::new()
    .write("foo.just", "bar:\n  echo bar\n")
    .justfile(
      "
        mod foo
      ",
    )
    .args(args)
    .stderr_regex("error: justfile does not contain submodule `fob(::bar)?`\nDid you mean `foo`\\?\n")
    .failure();
}

#[test]
fn probe_list_suggests_submodule() {
  probe_unknown_submodule(&["--list", "fob"]);
}

#[test]
fn probe_show_suggests_submodule() {
  probe_unknown_submodule(&["--show", "fob::bar"]);
}

#[test]
fn probe_usage_suggests_submodule() {
  probe_unknown_submodule(&["--usage", "fob::bar"]);
}

#[test]
fn probe_run_suggests_submodule() {
  probe_unknown_submodule(&["fob::bar"]);
}
