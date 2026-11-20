fn probe_cache_with_two_recipes() -> TempDir {
  let justfile = "
    [cache]
    [script]
    foo:
      echo foo

    [cache]
    [script]
    bar:
      echo bar
  ";

  let output = Test::new()
    .justfile(justfile)
    .env("JUST_UNSTABLE", "1")
    .arg("foo")
    .stdout("foo\n")
    .success();

  Test::with_tempdir(output.tempdir)
    .justfile(justfile)
    .env("JUST_UNSTABLE", "1")
    .arg("bar")
    .stdout("bar\n")
    .success()
    .tempdir
}

fn probe_cache_files(tempdir: &TempDir) -> Vec<String> {
  let mut contents = fs::read_dir(tempdir.path().join(".justcache"))
    .unwrap()
    .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
    .collect::<Vec<String>>();
  contents.sort();
  contents
}

#[test]
fn probe_clean_path_removes_entries_that_are_not_json() {
  let tempdir = probe_cache_with_two_recipes();

  fs::write(tempdir.path().join(".justcache/garbage.json"), "not json").unwrap();

  let output = Test::with_tempdir(tempdir)
    .env("JUST_UNSTABLE", "1")
    .args(["--clean", "foo"])
    .success();

  let files = probe_cache_files(&output.tempdir);
  assert_eq!(files.len(), 1, "{files:?}");
  assert!(files[0].contains("\"bar\""), "{files:?}");
}

#[test]
fn probe_clean_path_removes_entries_of_another_shape() {
  let tempdir = probe_cache_with_two_recipes();

  fs::write(tempdir.path().join(".justcache/shape.json"), "{\"probe\": [1, 2]}").unwrap();

  let output = Test::with_tempdir(tempdir)
    .env("JUST_UNSTABLE", "1")
    .args(["--clean", "foo"])
    .success();

  let files = probe_cache_files(&output.tempdir);
  assert_eq!(files.len(), 1, "{files:?}");
  assert!(files[0].contains("\"bar\""), "{files:?}");
}
