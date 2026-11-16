import QtQuick
import MUN.Shell

// The scene deploy/standby-frame.py runs in place of Main.qml: the shell's
// own Main.qml, compiled in, made as on the first start of a boot, its
// canvas grabbed before the start-up begins, in standby (qml/BootLayer.qml:
// the LED lit, held). OUT is replaced by the file to write (PPM); the
// journal gets STANDBY-SAVED or STANDBY-ERROR and why.
Item {
    function find(item, test) {
        if (!item)
            return null
        if (test(item))
            return item
        const children = item.children || []
        for (let i = 0; i < children.length; ++i) {
            const found = find(children[i], test)
            if (found)
                return found
        }
        return null
    }

    function fail(why) {
        console.log("STANDBY-ERROR " + why)
        Qt.exit(1)
    }

    Component.onCompleted: {
        const component = Qt.createComponent("qrc:/qt/qml/MUN/Shell/Main.qml")
        if (component.status !== Component.Ready)
            return fail(component.errorString())
        const main = component.createObject(null)
        const canvas = find(main.contentItem, it => it.width === Theme.canvasWidth && it.height === Theme.canvasHeight
                                                    && it.focus === true)
        const boot = find(canvas, it => it.standby !== undefined && it.canSkip !== undefined)
        if (!canvas || !boot)
            return fail("the start-up's layer is not where Main.qml has it")
        if (!boot.standby)
            return fail("the start-up's layer is not in standby")
        canvas.grabToImage(function (result) {
            if (!boot.standby)
                return fail("the start-up began before the frame was grabbed")
            if (!result.saveToFile(OUT))
                return fail("cannot write " + OUT)
            console.log("STANDBY-SAVED")
            Qt.exit(0)
        })
    }
}
