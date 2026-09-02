// Conformance fixture: known escapes and one function of known complexity.
@SuppressWarnings("unchecked")
public class Sample {
    @Disabled
    void skipped() {}

    int branchy(int n) {
        if (n == 1) { return 1; }
        if (n == 2) { return 2; }
        if (n == 3) { return 3; }
        if (n == 4) { return 4; }
        return 0;
    }
}
