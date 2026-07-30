package app_test

import (
	"reflect"
	"strings"
	"testing"

	"github.com/Plabrum/tt/internal/app"
)

// entPkg is the prefix no type in the API's surface may come from.
const entPkg = "github.com/Plabrum/tt/ent"

// TestAPIHidesEnt walks every parameter and return type of every API method,
// through slices, maps, pointers and struct fields, asserting none is declared
// in ent.
//
// The frontends are what this protects: an ent entity reaching cli/ or ui/ puts
// query builders and lazily-loaded edges in render code, where a nil edge slice
// means either "no rows" or "not loaded" and a renderer cannot tell which. The
// import graph alone would not catch it, since a method could return a type
// that merely embeds one.
func TestAPIHidesEnt(t *testing.T) {
	t.Parallel()

	typ := reflect.TypeOf(&app.API{})
	if typ.NumMethod() == 0 {
		t.Fatal("API has no methods — this test would pass vacuously")
	}
	for i := range typ.NumMethod() {
		m := typ.Method(i)
		seen := map[reflect.Type]bool{}
		for j := range m.Type.NumIn() {
			walkForEnt(t, m.Name, m.Type.In(j), seen)
		}
		for j := range m.Type.NumOut() {
			walkForEnt(t, m.Name, m.Type.Out(j), seen)
		}
	}
}

func walkForEnt(t *testing.T, method string, typ reflect.Type, seen map[reflect.Type]bool) {
	t.Helper()
	if seen[typ] {
		return
	}
	seen[typ] = true

	if pkg := typ.PkgPath(); pkg == entPkg || strings.HasPrefix(pkg, entPkg+"/") {
		t.Errorf("API.%s exposes %s.%s", method, pkg, typ.Name())
		return
	}

	switch typ.Kind() {
	case reflect.Pointer, reflect.Slice, reflect.Array, reflect.Chan:
		walkForEnt(t, method, typ.Elem(), seen)
	case reflect.Map:
		walkForEnt(t, method, typ.Key(), seen)
		walkForEnt(t, method, typ.Elem(), seen)
	case reflect.Struct:
		for i := range typ.NumField() {
			// Unexported fields are out of a frontend's reach whatever they
			// hold, and skipping them is what lets API itself — the receiver of
			// every method here — keep its ent client.
			if f := typ.Field(i); f.IsExported() {
				walkForEnt(t, method, f.Type, seen)
			}
		}
	}
}
