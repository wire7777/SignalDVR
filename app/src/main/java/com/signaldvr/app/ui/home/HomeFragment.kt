package com.signaldvr.app.ui.home

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.signaldvr.app.R
import com.signaldvr.app.ui.guide.EpgFragment
import com.signaldvr.app.ui.library.LibraryFragment
import com.signaldvr.app.ui.live.LiveTvFragment
import com.signaldvr.app.ui.recordings.RecordingsFragment


data class HomeItem(val icon: String, val title: String, val subtitle: String, val id: Int)

class HomeFragment : Fragment() {

    private val items = listOf(
        HomeItem("📺", "Live TV", "Watch live channels with DVR", 1),
        HomeItem("📚", "DVR Library", "Live, buffered, saved, and recorded", 2),
        HomeItem("📅", "TV Guide", "Full EPG programme grid", 3),
        HomeItem("⏺", "Recordings", "Recorded programmes", 4),
    )

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, b: Bundle?
    ): View = inflater.inflate(R.layout.fragment_home, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        val recycler = view.findViewById<RecyclerView>(R.id.home_grid)
        recycler.layoutManager = GridLayoutManager(requireContext(), 2)
        recycler.adapter = HomeAdapter(items) { item ->
            val fragment = when (item.id) {
                1 -> LiveTvFragment()
                2 -> LibraryFragment()
                3 -> EpgFragment()
                4 -> RecordingsFragment()
                else -> return@HomeAdapter
            }
            parentFragmentManager.beginTransaction()
                .replace(R.id.main_container, fragment)
                .addToBackStack(null)
                .commit()
        }
        recycler.requestFocus()
    }
}

class HomeAdapter(
    private val items: List<HomeItem>,
    private val onClick: (HomeItem) -> Unit,
) : RecyclerView.Adapter<HomeAdapter.VH>() {

    inner class VH(view: View) : RecyclerView.ViewHolder(view) {
        val icon: TextView = view.findViewById(R.id.home_item_icon)
        val title: TextView = view.findViewById(R.id.home_item_title)
        val subtitle: TextView = view.findViewById(R.id.home_item_subtitle)

        init {
            view.isFocusable = true
            view.setOnClickListener {
                val pos = bindingAdapterPosition
                if (pos != RecyclerView.NO_POSITION) onClick(items[pos])
            }
            view.setOnFocusChangeListener { v, hasFocus ->
                v.setBackgroundResource(
                    if (hasFocus) R.drawable.bg_channel_focused
                    else R.drawable.bg_channel_normal
                )
            }
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_home_card, parent, false)
        return VH(v)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = items[position]
        holder.icon.text = item.icon
        holder.title.text = item.title
        holder.subtitle.text = item.subtitle
    }

    override fun getItemCount() = items.size
}
