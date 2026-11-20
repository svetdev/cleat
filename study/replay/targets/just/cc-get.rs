#[test]
fn probe_reverse_function() {
  Test::new()
    .justfile(
      "
        foo:
          @echo '{{ reverse('hello') }}'
      ",
    )
    .stdout("olleh\n")
    .success();
}

#[test]
fn probe_reverse_function_keeps_characters_whole() {
  Test::new()
    .justfile(
      "
        foo:
          @echo '{{ reverse('añb') }}{{ reverse('') }}'
      ",
    )
    .stdout("bña\n")
    .success();
}
