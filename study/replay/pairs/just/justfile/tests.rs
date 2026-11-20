#[test]
fn probe_first_command_evaluates_unused_assignments() {
  Test::new()
    .justfile(
      "
        x := `exit 1`

        foo:
      ",
    )
    .args(["--command", "echo", "hello"])
    .stderr(
      "
        error: backtick failed with exit code 1
         ——▶ justfile:1:6
          │
        1 │ x := `exit 1`
          │      ^^^^^^^^
      ",
    )
    .failure();
}

#[test]
fn probe_second_evaluate_variable_evaluates_other_assignments() {
  Test::new()
    .justfile(
      "
        x := `exit 1`
        y := 'bar'

        foo:
      ",
    )
    .args(["--evaluate", "y"])
    .stderr(
      "
        error: backtick failed with exit code 1
         ——▶ justfile:1:6
          │
        1 │ x := `exit 1`
          │      ^^^^^^^^
      ",
    )
    .failure();
}
