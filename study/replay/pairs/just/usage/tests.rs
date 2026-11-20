#[test]
fn probe_first_arguments_are_indented_four_spaces() {
  Test::new()
    .justfile("foo bar baz='qux':")
    .args(["--usage", "foo"])
    .stdout("Usage: just foo bar [baz]\n\nArguments:\n    bar\n    [baz] [default: 'qux']\n")
    .success();
}

#[test]
fn probe_second_options_are_indented_four_spaces() {
  Test::new()
    .justfile(
      "
        [arg('a', short='a')]
        [arg('b', long='bee', help='hello')]
        foo a b:
      ",
    )
    .args(["--usage", "foo"])
    .stdout_regex("Usage: just foo \\[OPTIONS\\]\n+Options:\n    -a a\n        --bee b hello\n")
    .success();
}
