#[test]
fn probe_attribute_takes_no_arguments() {
  Test::new()
    .justfile(
      "
        [private('foo')]
        foo:
          exit 1
      ",
    )
    .stderr_regex("error: attribute `private` got 1 argument but takes no arguments\n.*")
    .failure();
}

#[test]
fn probe_dependency_takes_no_arguments() {
  Test::new()
    .justfile(
      "
        foo:

        bar: (foo 'baz')
      ",
    )
    .arg("bar")
    .stderr_regex("error: dependency `foo` got 1 argument but takes no arguments\n.*")
    .failure();
}
